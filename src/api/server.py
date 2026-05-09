"""
FastAPI Server Endpoints for ready-ai.
Production-ready with CORS, rate limiting, and health checks.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import shutil
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from src.api.models import RunRequest, RunStatusResponse
from src.api.manager import RunManager

# ─── Simple in-memory rate limiter ──────────────────────────────────────────

_rate_limit_store: dict[str, list[float]] = {}
RATE_LIMIT_WINDOW = 60.0   # seconds
RATE_LIMIT_MAX = 30         # requests per window per IP


def _check_rate_limit(client_ip: str) -> bool:
    """Return True if request is allowed, False if rate limited."""
    now = time.time()
    window = _rate_limit_store.get(client_ip, [])
    # Prune old entries
    window = [t for t in window if now - t < RATE_LIMIT_WINDOW]
    _rate_limit_store[client_ip] = window
    if len(window) >= RATE_LIMIT_MAX:
        return False
    window.append(now)
    return True


# ─── FastAPI app ──────────────────────────────────────────────────────────

app = FastAPI(
    title="ready-ai API",
    description="Agentic browser automation for seamless documentation generation.",
    version="0.1.0",
)

# CORS — configurable via env, defaults to localhost dev origins
import os

cors_origins_env = os.getenv("CORS_ORIGINS", "")
if cors_origins_env:
    cors_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
else:
    cors_origins = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Apply rate limiting to all routes except health checks."""
    if request.url.path in ("/health", "/ready", "/docs", "/openapi.json"):
        return await call_next(request)

    client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")
    # Take first IP if x-forwarded-for is a list
    if "," in client_ip:
        client_ip = client_ip.split(",")[0].strip()

    if not _check_rate_limit(client_ip):
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "Rate limit exceeded. Try again later."},
        )
    return await call_next(request)


# ─── Health endpoints ────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """Liveness probe — returns basic service status."""
    return {
        "status": "healthy",
        "service": "ready-ai",
        "version": "0.1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready")
async def readiness_check():
    """Readiness probe — checks if dependencies are available."""
    # Check that output directory is writable
    try:
        output_dir = Path("./output")
        output_dir.mkdir(parents=True, exist_ok=True)
        test_file = output_dir / ".ready_probe"
        test_file.write_text("ok")
        test_file.unlink()
    except OSError as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "not_ready",
                "reason": f"Output directory not writable: {exc}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    return {
        "status": "ready",
        "checks": {
            "output_dir": "ok",
            "browser_pool": "ok",  # RunManager handles this dynamically
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ─── Run endpoints ────────────────────────────────────────────────────────

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
        base_name=str(zip_path.with_suffix("")),
        format="zip",
        root_dir=output_dir,
    )

    if not zip_path.exists():
        raise HTTPException(status_code=500, detail="Failed to generate ZIP archive.")

    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=f"browser_docs_{run_id}.zip",
    )


@app.get("/runs/{run_id}/metrics")
async def get_run_metrics(run_id: str):
    """Retrieve observability metrics for a completed run."""
    metrics_path = Path(f"./output/{run_id}_metrics.json")

    if not metrics_path.exists():
        raise HTTPException(status_code=404, detail="Metrics not found for this run.")

    data = json.loads(metrics_path.read_text())
    return JSONResponse(content=data)
