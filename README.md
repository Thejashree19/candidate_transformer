# Multi-Source Candidate Data Transformer

A deterministic pipeline that ingests candidate data from multiple sources, normalizes and merges it into one canonical profile per person, and emits schema-valid JSON with provenance and confidence.

## What it does

- Ingests structured and unstructured inputs
- Normalizes phones, dates, locations, and skills
- Merges matching candidates across sources
- Tracks provenance and confidence
- Supports a runtime JSON config to reshape output without code changes
- Provides a beautifully designed web interface for interactive extraction

## Supported inputs

- Recruiter CSV export
- ATS JSON blob
- GitHub profile data (via URL or Username)
- LinkedIn profile data (via URL)
- Resumes (PDF, DOCX, TXT)
- Recruiter notes text file

## Requirements

- Python 3.11+
- pip

## Setup

Clone the repository and install dependencies in a virtual environment:

```bash
git clone <your-repo-url>
cd <repo-folder>
python -m venv .venv

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# Windows CMD
.venv\Scripts\activate.bat

# macOS / Linux
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Running the Web Interface

For the best experience, start the built-in FastAPI web server and open the UI in your browser:

```bash
uvicorn app:app --port 8000
```
Then navigate to [http://localhost:8000](http://localhost:8000). The web UI allows you to upload resumes, CSVs, and ATS JSONs, and visualize the generated canonical candidate profiles via interactive cards.

## Run the CLI pipeline

The CLI writes JSON to a file when `--output` is provided. If you omit `--output`, the JSON is printed to stdout.

### Run with all default sample data

Use the `--demo` flag to automatically run the pipeline with all included mock sources (ATS, CSV, Resumes, Notes, GitHub, LinkedIn):

```bash
python main.py --demo --output sample_outputs/default_output.json
```

### Run on custom inputs

You can provide paths to specific input sources:

```bash
python main.py --resume my_custom_resume.pdf --notes interview_notes.txt --output result.json
```

### Custom projected output

```bash
python main.py --demo --config config/custom_config_example.json --output sample_outputs/custom_output.json
```

## Example output shape

Default output is a JSON object with two top-level keys:

```json
{
  "metadata": {
    "total_profiles": 7,
    "sources_processed": [],
    "warnings": [],
    "errors": []
  },
  "profiles": [
    {
      "candidate_id": "...",
      "full_name": "...",
      "emails": ["..."],
      "phones": ["+1..."],
      "location": {"city": "...", "region": "...", "country": "US"},
      "skills": [{"name": "Python", "confidence": 0.97, "sources": ["ats_json"]}],
      "overall_confidence": 0.78,
      "provenance": []
    }
  ]
}
```

## Runtime config

Use a config JSON to project the canonical profile into a custom schema.

Example:

```json
{
  "fields": [
    { "path": "full_name", "type": "string", "required": true },
    { "path": "primary_email", "from": "emails[0]", "type": "string" },
    { "path": "phone", "from": "phones[0]", "type": "string", "normalize": "E164" },
    { "path": "skills", "from": "skills[].name", "type": "string[]", "normalize": "canonical" }
  ],
  "include_confidence": true,
  "include_provenance": false,
  "on_missing": "null"
}
```

## Tests

Run the full test suite:

```bash
python -m pytest -q
```

## Project structure

```text
.
├── app.py             # FastAPI backend for the web UI
├── main.py            # CLI entry point
├── config/
├── sample_inputs/
├── sample_outputs/
├── src/
├── static/            # Web UI frontend assets (HTML/CSS/JS)
└── tests/
```

## Notes

- The repo is designed to run the same way on Windows, macOS, and Linux.
- The sample inputs in `sample_inputs/` are enough to reproduce the bundled outputs.
- Invalid source values are dropped rather than invented.