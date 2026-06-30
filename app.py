import tempfile
import os
import shutil
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, File, UploadFile, Form, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.pipeline import Pipeline
from src.models import OutputConfig

app = FastAPI(title="Multi-Source Candidate Data Transformer")

# Create a static directory if it doesn't exist
Path("static").mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def read_index():
    index_path = Path("static/index.html")
    if index_path.exists():
        return index_path.read_text(encoding="utf-8")
    return "<h1>UI is not built yet</h1>"


def cleanup_temp_dir(dir_path: str):
    """Remove temporary directory after pipeline completes."""
    try:
        shutil.rmtree(dir_path)
    except Exception as e:
        pass


@app.post("/api/extract")
async def extract_candidate_data(
    request: Request,
    background_tasks: BackgroundTasks
):
    """
    Endpoint to process uploaded files and URLs.
    Writes them to a temporary directory, runs the Pipeline, and cleans up.
    """
    try:
        form = await request.form()
    except Exception as e:
        return JSONResponse(status_code=400, content={"detail": f"Error parsing form: {str(e)}"})

    temp_dir = tempfile.mkdtemp()
    background_tasks.add_task(cleanup_temp_dir, temp_dir)

    csv_path = None
    ats_path = None
    notes_path = None
    resume_paths = []

    # Helper to save upload file
    async def save_upload(upload_file: UploadFile, suffix: str) -> str:
        filepath = os.path.join(temp_dir, f"upload_{suffix}_{upload_file.filename}")
        content = await upload_file.read()
        with open(filepath, "wb") as f:
            f.write(content)
        return filepath

    csv = form.get("csv")
    if csv and hasattr(csv, "filename") and csv.filename:
        csv_path = await save_upload(csv, "csv")
        
    ats = form.get("ats")
    if ats and hasattr(ats, "filename") and ats.filename:
        ats_path = await save_upload(ats, "ats")
        
    notes = form.get("notes")
    if notes and hasattr(notes, "filename") and notes.filename:
        notes_path = await save_upload(notes, "notes")
        
    resumes = form.getlist("resumes")
    for idx, resume_file in enumerate(resumes):
        if hasattr(resume_file, "filename") and resume_file.filename:
            r_path = await save_upload(resume_file, f"resume_{idx}")
            resume_paths.append(r_path)

    github_val = form.get("github")
    github_usernames = None
    if isinstance(github_val, str) and github_val.strip():
        github_usernames = [u.strip() for u in github_val.split(",") if u.strip()]

    linkedin_val = form.get("linkedin")
    linkedin_urls = None
    if isinstance(linkedin_val, str) and linkedin_val.strip():
        linkedin_urls = [u.strip() for u in linkedin_val.split(",") if u.strip()]

    # Use offline caches if they exist
    github_cache = "sample_inputs/github_cache.json" if Path("sample_inputs/github_cache.json").exists() else None
    linkedin_cache = "sample_inputs/linkedin_cache.json" if Path("sample_inputs/linkedin_cache.json").exists() else None

    # Run pipeline
    pipeline = Pipeline(github_cache_path=github_cache)
    result = pipeline.run(
        csv_path=csv_path,
        ats_path=ats_path,
        github_usernames=github_usernames,
        linkedin_urls=linkedin_urls,
        notes_path=notes_path,
        resume_paths=resume_paths if resume_paths else None,
        config=OutputConfig.default(),
        linkedin_cache_path=linkedin_cache,
    )

    # Convert result to JSON output shape
    output_data = {
        "metadata": {
            "total_profiles": len(result.profiles),
            "sources_processed": [
                {
                    "type": s.source_type.value,
                    "filename": Path(s.path).name if "://" not in s.path else s.path,
                    "status": s.status.value,
                }
                for s in result.source_statuses
            ],
            "warnings": result.warnings,
            "errors": result.errors,
        },
        "profiles": result.profiles,
    }

    return JSONResponse(content=output_data)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
