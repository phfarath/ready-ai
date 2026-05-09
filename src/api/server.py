"""
FastAPI Server Endpoints for ready-ai.
Supports: single runs, deploy webhooks, batch configuration, and output retrieval.
"""

import json
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
import shutil

from src.api.models import (
    RunRequest, RunStatusResponse,
    DeployWebhookPayload, BatchRunResponse, BatchStatusResponse,
    BatchConfig,
)
from src.api.manager import RunManager

app = FastAPI(
    title="ready-ai API",
    description="Agentic browser automation for seamless documentation generation.",
    version="0.2.0",
)

# ─── Single Run Endpoints ─────────────────────────────────────────────

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


# ─── Deploy Webhook Endpoint ──────────────────────────────────────────

@app.post("/webhooks/deploy", response_model=BatchRunResponse, status_code=202)
async def deploy_webhook(payload: DeployWebhookPayload):
    """
    Receive a deploy webhook and kick off documentation generation
    for all configured flows.
    
    Example payload:
    ```json
    {
      "app_version": "2.3.1",
      "git_commit": "abc1234",
      "deployed_at": "2026-05-09T14:00:00Z",
      "base_url": "https://app.example.com",
      "flows": [
        {"goal": "Document login", "path": "/login", "run_id": "login"},
        {"goal": "Document onboarding", "path": "/welcome"}
      ]
    }
    ```
    """
    batch_id = str(uuid.uuid4())[:8]
    
    # Convert webhook payload to BatchConfig
    config = BatchConfig(
        app_version=payload.app_version,
        git_commit=payload.git_commit,
        deployed_at=payload.deployed_at,
        base_url=payload.base_url,
        model=payload.model,
        headless=payload.headless,
        cookies_file=payload.cookies_file,
        flows=[
            BatchConfigFlow(
                goal=f.goal,
                path=f.path,
                run_id=f.run_id,
                title=f.title,
                language=f.language,
            ) for f in payload.flows
        ],
    )
    
    try:
        result = await RunManager.start_batch(config, batch_id)
        return BatchRunResponse(
            batch_id=batch_id,
            total_flows=result["total_flows"],
            accepted=result["accepted"],
            rejected=result["rejected"],
            run_ids=result["run_ids"],
            status=result["status"],
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Batch processing failed: {exc}") from exc


# ─── Batch Endpoints ────────────────────────────────────────────────

@app.post("/batches", response_model=BatchRunResponse, status_code=202)
async def create_batch(config: BatchConfig):
    """Start a batch of documentation runs from a YAML/TOML config."""
    batch_id = str(uuid.uuid4())[:8]
    
    try:
        result = await RunManager.start_batch(config, batch_id)
        return BatchRunResponse(
            batch_id=batch_id,
            total_flows=result["total_flows"],
            accepted=result["accepted"],
            rejected=result["rejected"],
            run_ids=result["run_ids"],
            status=result["status"],
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Batch processing failed: {exc}") from exc

@app.get("/batches/{batch_id}", response_model=BatchStatusResponse)
async def get_batch_status(batch_id: str):
    """Poll the status of a batch run."""
    status = RunManager.get_batch_status(batch_id)
    if not status:
        raise HTTPException(status_code=404, detail="Batch not found.")
    
    return BatchStatusResponse(
        batch_id=batch_id,
        total_flows=status["total_flows"],
        completed=status["completed"],
        failed=status["failed"],
        running=status["running"],
        pending=status["pending"],
        statuses=[RunStatusResponse(**s) for s in status["statuses"]],
    )
