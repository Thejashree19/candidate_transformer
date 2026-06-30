import tempfile
import os
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import uvicorn

from src.pipeline import Pipeline
from src.models import OutputConfig

app = FastAPI(title="Candidate Transformer API")

# Mount static files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/", response_class=HTMLResponse)
async def read_index():
    with open(static_dir / "index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.post("/api/transform")
async def transform(
    csv_file: Optional[UploadFile] = File(None),
    ats_file: Optional[UploadFile] = File(None),
    notes_file: Optional[UploadFile] = File(None),
    resume_files: List[UploadFile] = File(None)
):
    temp_dir = tempfile.TemporaryDirectory()
    temp_path = Path(temp_dir.name)
    
    csv_path = None
    ats_path = None
    notes_path = None
    resume_paths = []

    if csv_file and csv_file.filename:
        csv_path = temp_path / csv_file.filename
        csv_path.write_bytes(await csv_file.read())
        csv_path = str(csv_path)

    if ats_file and ats_file.filename:
        ats_path = temp_path / ats_file.filename
        ats_path.write_bytes(await ats_file.read())
        ats_path = str(ats_path)

    if notes_file and notes_file.filename:
        notes_path = temp_path / notes_file.filename
        notes_path.write_bytes(await notes_file.read())
        notes_path = str(notes_path)

    if resume_files:
        for rf in resume_files:
            if rf.filename:
                r_path = temp_path / rf.filename
                r_path.write_bytes(await rf.read())
                resume_paths.append(str(r_path))

    pipeline = Pipeline()
    config = OutputConfig.default()
    
    result = pipeline.run(
        csv_path=csv_path,
        ats_path=ats_path,
        notes_path=notes_path,
        resume_paths=resume_paths if resume_paths else None,
        config=config
    )
    
    return result.model_dump()

if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
