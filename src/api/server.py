"""
FastAPI Server Endpoints for ready-ai.
Starts the server and exposes runs/ endpoints.
"""

import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
import shutil

from src.api.models import RunRequest, RunStatusResponse
from src.api.manager import RunManager

app = FastAPI(
    title="ready-ai API",
    description="Agentic browser automation for seamless documentation generation.",
    version="0.1.0",
)

@app.post("/runs", response_model=RunStatusResponse)
async def create_run(req: RunRequest):
    """Trigger a new documentation run."""
    try:
        run_id = await RunManager.start_run(req)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    status = RunManager.get_status(run_id)
    if not status:
        raise HTTPException(status_code=500, detail="Failed to initialize run state.")
    return status

@app.get("/runs/{run_id}", response_model=RunStatusResponse)
async def get_run_status(run_id: str):
    """Poll the status of a specific run."""
    status = RunManager.get_status(run_id)
    if not status:
        raise HTTPException(status_code=404, detail="Run not found.")
    return status

@app.get("/runs/{run_id}/output")
async def get_run_output(run_id: str):
    """
    Retrieve the finished markdown and screenshots as a ZIP file.
    Assumes `AgenticLoop` wrote `docs.md` and `screenshots/` to `./output/<run_id>/`
    """
    output_dir = Path(f"./output/{run_id}")
    
    if not output_dir.exists():
        raise HTTPException(status_code=404, detail="Output directory not found.")
        
    zip_path = Path(f"./output/{run_id}.zip")
    
    # Create zip archive of the output directory
    shutil.make_archive(
        base_name=str(zip_path.with_suffix('')), 
        format='zip', 
        root_dir=output_dir
    )
    
    if not zip_path.exists():
        raise HTTPException(status_code=500, detail="Failed to generate ZIP archive.")
        
    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=f"browser_docs_{run_id}.zip"
    )

@app.get("/runs/{run_id}/metrics")
async def get_run_metrics(run_id: str):
    """Retrieve observability metrics for a completed run."""
    metrics_path = Path(f"./output/{run_id}_metrics.json")

    if not metrics_path.exists():
        raise HTTPException(status_code=404, detail="Metrics not found for this run.")

    data = json.loads(metrics_path.read_text())
    return JSONResponse(content=data)
