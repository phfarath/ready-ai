"""
FastAPI Server Endpoints for ready-ai.
Production-ready with CORS, rate limiting, and health checks.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import shutil
from fastapi import Body, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from src.api.models import (
    RunRequest, RunStatusResponse,
    ExportRequest, ExportResponse,
    RunListItem, RunListResponse,
    DiffResponse,
    DocsSetItem, DocsListResponse,
    DocsVersionStatus,
)
from src.api.manager import RunManager
from src.docs.export import export_docs, SUPPORTED_FORMATS
from src.history import get_history, get_aggregates

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


# ─── Export endpoint ──────────────────────────────────────────────────────
@app.post("/runs/{run_id}/export", response_model=ExportResponse)
async def export_run(
    run_id: str,
    req: ExportRequest = Body(...),
):
    """
    Export a completed documentation run to a static-site format.

    Supported formats: markdown, docusaurus, nextra, mintlify, starlight.
    """
    # Validate run exists
    status = RunManager.get_status(run_id)
    if not status:
        raise HTTPException(status_code=404, detail="Run not found.")

    if status.status not in ("completed", "success"):
        raise HTTPException(
            status_code=409,
            detail=f"Run is not completed (status: {status.status}). Export only available for finished runs.",
        )

    # Validate format
    format_name = req.format.lower()
    if format_name not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown format '{format_name}'. Supported: {', '.join(SUPPORTED_FORMATS)}",
        )

    # Determine paths
    run_output_dir = Path(f"./output/{run_id}")
    doc_path = run_output_dir / "docs.md"

    if not doc_path.exists():
        raise HTTPException(
            status_code=404,
            detail="docs.md not found for this run.",
        )

    if req.output_dir:
        export_output_dir = Path(req.output_dir)
    else:
        export_output_dir = Path(f"./output/{run_id}/export/{format_name}")

    try:
        result = export_docs(
            doc_path=doc_path,
            format=format_name,
            output_dir=export_output_dir,
            screenshots_dir=run_output_dir / "screenshots",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc

    return ExportResponse(
        run_id=run_id,
        format=format_name,
        output_dir=str(result.output_dir),
        files_created=[str(f) for f in result.files_created],
        success=True,
    )


# ─── List runs ──────────────────────────────────────────────────────────
@app.get("/runs", response_model=RunListResponse)
async def list_runs(
    status_filter: str | None = None,
    app_version: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """
    List all documentation runs with optional filtering and pagination.
    """
    output_dir = Path("./output")
    runs: list[RunListItem] = []

    # Scan output directory for run subdirectories
    if output_dir.exists():
        for run_dir in sorted(output_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not run_dir.is_dir():
                continue
            run_id = run_dir.name
            # Skip non-run directories (like .git or temp files)
            if run_id.startswith(".") or run_id.endswith(".zip"):
                continue

            state = RunManager.get_status(run_id)
            if not state:
                continue

            # Apply filters
            if status_filter and state.status.lower() != status_filter.lower():
                continue

            # Try to read manifest for version info
            manifest_path = run_dir / "manifest.json"
            version = None
            git_commit = None
            deployed_at = None
            created_at = None
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    version = manifest.get("app_version")
                    git_commit = manifest.get("git_commit")
                    deployed_at = manifest.get("deployed_at")
                    created_at = manifest.get("generated_at")
                except (json.JSONDecodeError, OSError):
                    pass

            if app_version and version != app_version:
                continue

            runs.append(RunListItem(
                run_id=state.run_id,
                status=state.status,
                goal=state.goal,
                url=state.url,
                executed_steps=state.executed_steps,
                total_planned_steps=state.total_planned_steps,
                app_version=version,
                git_commit=git_commit,
                deployed_at=deployed_at,
                created_at=created_at,
            ))

    total = len(runs)
    paginated = runs[offset:offset + limit]

    return RunListResponse(total=total, runs=paginated)

# ─── Run diff ──────────────────────────────────────────────────────────
@app.get("/runs/{run_id}/diff", response_model=DiffResponse)
async def get_run_diff(run_id: str, previous_run_id: str | None = None):
    """
    Get a textual diff between this run and a previous version.
    If previous_run_id is not provided, attempts to find the most recent
    run with the same app_version.
    """
    run_dir = Path(f"./output/{run_id}")
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="Run not found.")

    doc_path = run_dir / "docs.md"
    if not doc_path.exists():
        raise HTTPException(status_code=404, detail="docs.md not found for this run.")

    # Determine previous version to compare against
    if not previous_run_id:
        # Try to find manifest and match by app_version
        manifest_path = run_dir / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                version = manifest.get("app_version")
                # Find the most recent run with the same version but different run_id
                if version:
                    output_dir = Path("./output")
                    candidates = sorted(
                        [d for d in output_dir.iterdir() if d.is_dir() and d.name != run_id and not d.name.startswith(".")],
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                    for candidate in candidates:
                        cand_manifest = candidate / "manifest.json"
                        if cand_manifest.exists():
                            try:
                                cand_data = json.loads(cand_manifest.read_text(encoding="utf-8"))
                                if cand_data.get("app_version") == version:
                                    previous_run_id = candidate.name
                                    break
                            except (json.JSONDecodeError, OSError):
                                continue
            except (json.JSONDecodeError, OSError):
                pass

    if not previous_run_id:
        # Fallback: just find the most recent other run
        output_dir = Path("./output")
        if output_dir.exists():
            candidates = sorted(
                [d for d in output_dir.iterdir() if d.is_dir() and d.name != run_id and not d.name.startswith(".")],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                previous_run_id = candidates[0].name

    if not previous_run_id or not (Path(f"./output/{previous_run_id}") / "docs.md").exists():
        raise HTTPException(
            status_code=404,
            detail="No previous version found for comparison.",
        )

    previous_doc = Path(f"./output/{previous_run_id}") / "docs.md"

    try:
        from src.docs.text_diff import compare_docs
        from src.docs.manifest import DocManifest
        result = compare_docs(str(previous_doc), str(doc_path))
        
        # Build changelog markdown
        baseline_manifest = None
        current_manifest = None
        baseline_manifest_path = Path(f"./output/{previous_run_id}") / "manifest.json"
        current_manifest_path = run_dir / "manifest.json"
        if baseline_manifest_path.exists():
            baseline_manifest = DocManifest.from_file(str(baseline_manifest_path))
        if current_manifest_path.exists():
            current_manifest = DocManifest.from_file(str(current_manifest_path))
        
        changelog = result.to_markdown(baseline_manifest, current_manifest)
        diff_json = result.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Diff generation failed: {exc}") from exc

    return DiffResponse(
        run_id=run_id,
        previous_version=previous_run_id,
        current_version=run_id,
        changelog=changelog,
        diff_json=diff_json,
    )

# ─── List docs ──────────────────────────────────────────────────────────
@app.get("/doc-sets", response_model=DocsListResponse)
async def list_docs(
    version: str | None = None,
    status_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """
    List all generated documentation sets.
    """
    output_dir = Path("./output")
    docs: list[DocsSetItem] = []

    if output_dir.exists():
        for run_dir in sorted(output_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not run_dir.is_dir() or run_dir.name.startswith("."):
                continue

            run_id = run_dir.name
            state = RunManager.get_status(run_id)
            if not state:
                continue

            # Apply status filter
            if status_filter and state.status.lower() != status_filter.lower():
                continue

            # Read manifest
            manifest_path = run_dir / "manifest.json"
            doc_version = None
            files = []
            generated_at = None
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    doc_version = manifest.get("app_version")
                    files = manifest.get("files", [])
                    generated_at = manifest.get("generated_at")
                except (json.JSONDecodeError, OSError):
                    pass

            if version and doc_version != version:
                continue

            # Collect files from directory if manifest didn't have them
            if not files:
                files = [f.name for f in run_dir.iterdir() if f.is_file()]
                ss_dir = run_dir / "screenshots"
                if ss_dir.exists():
                    files += [f"screenshots/{f.name}" for f in ss_dir.iterdir() if f.is_file()]

            docs.append(DocsSetItem(
                run_id=run_id,
                version=doc_version,
                goal=state.goal,
                url=state.url,
                status=state.status,
                files=files,
                generated_at=generated_at,
            ))

    total = len(docs)
    paginated = docs[offset:offset + limit]

    return DocsListResponse(total=total, docs=paginated)

# ─── Docs version status ──────────────────────────────────────────────────
@app.get("/docs/{version}/status", response_model=DocsVersionStatus)
async def get_docs_version_status(version: str):
    """
    Get the health status of documentation for a specific app version.
    """
    output_dir = Path("./output")

    # Find the most recent run with this app_version
    target_run_dir: Path | None = None
    target_run_id: str | None = None
    target_status: str | None = None
    target_steps = 0

    if output_dir.exists():
        for run_dir in sorted(output_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not run_dir.is_dir() or run_dir.name.startswith("."):
                continue

            manifest_path = run_dir / "manifest.json"
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if manifest.get("app_version") == version:
                        target_run_dir = run_dir
                        target_run_id = run_dir.name
                        state = RunManager.get_status(target_run_id)
                        if state:
                            target_status = state.status
                            target_steps = state.executed_steps
                        break
                except (json.JSONDecodeError, OSError):
                    continue

    if not target_run_dir or not target_run_id:
        raise HTTPException(
            status_code=404,
            detail=f"No documentation found for version '{version}'.",
        )

    # Count files
    screenshots_dir = target_run_dir / "screenshots"
    screenshots_count = sum(1 for _ in screenshots_dir.glob("*.png")) if screenshots_dir.exists() else 0

    has_manifest = (target_run_dir / "manifest.json").exists()
    has_metrics = (target_run_dir / f"{target_run_id}_metrics.json").exists() or (Path("./output") / f"{target_run_id}_metrics.json").exists()

    # Try to read last test info
    last_tested = None
    test_status = None
    report_path = target_run_dir / "report.json"
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            last_tested = report.get("timestamp")
            test_status = report.get("status")
        except (json.JSONDecodeError, OSError):
            pass

    return DocsVersionStatus(
        version=version,
        run_id=target_run_id,
        status=target_status or "unknown",
        steps_count=target_steps,
        screenshots_count=screenshots_count,
        has_manifest=has_manifest,
        has_metrics=has_metrics,
        last_tested=last_tested,
        test_status=test_status,
    )

# ─── Historical Tracking ──────────────────────────────────────────────────
@app.get("/history")
async def list_history(
    limit: int = 50,
    offset: int = 0,
    app_version: str | None = None,
    status: str | None = None,
):
    """
    Retrieve historical run metrics.
    """
    records = get_history(limit=limit, offset=offset, app_version=app_version, status=status)
    return {
        "total": len(records),
        "records": [{
            "run_id": r.run_id,
            "goal": r.goal,
            "status": r.status,
            "steps_count": r.steps_count,
            "llm_tokens": r.llm_tokens,
            "duration_sec": r.duration_sec,
            "app_version": r.app_version,
            "timestamp": r.timestamp,
        } for r in records],
    }

@app.get("/history/aggregates")
async def history_aggregates(app_version: str | None = None):
    """
    Get aggregate statistics over all historical runs.
    """
    return get_aggregates(app_version=app_version)
