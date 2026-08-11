"""
FastAPI Server Endpoints for ready-ai.
Production-ready with CORS, rate limiting, API key auth, and health checks.
"""

import asyncio
import hmac
import json
import logging
import os
import signal
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import shutil
from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query, Request, Path as FPath, status
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

logger = logging.getLogger(__name__)

# ─── API Key Authentication ───────────────────────────────────────────────────

_AUTH_DISABLED = os.getenv("AUTH_DISABLED", "").lower() in ("true", "1", "yes")


def _load_api_keys() -> set[str]:
    """Load authorized API keys from env vars."""
    keys_env = os.getenv("READY_AI_API_KEYS", "")
    if keys_env:
        return {k.strip() for k in keys_env.split(",") if k.strip()}
    single_key = os.getenv("READY_AI_API_KEY", "")
    if single_key:
        return {single_key}
    return set()


_READY_AI_API_KEYS = _load_api_keys()


def _is_valid_api_key(provided_key: str) -> bool:
    """Validate an API key using constant-time comparison.

    Iterates all configured keys and uses ``hmac.compare_digest`` for each
    comparison to prevent timing side-channel attacks.
    """
    if not provided_key:
        return False
    for configured_key in _READY_AI_API_KEYS:
        if hmac.compare_digest(provided_key, configured_key):
            return True
    return False


# ─── Simple in-memory rate limiter ──────────────────────────────────────────

# ─── Simple in-memory rate limiter ──────────────────────────────────────────

class TokenBucketRateLimiter:
    """Token bucket rate limiter with per-tier limits and automatic cleanup.

    Supports:
    - Per-IP limiting (default)
    - Per-API-key limiting when AUTH is enabled
    - Tiered limits: health/ready (unlimited), batch runs (strict), standard API (moderate)
    - Automatic stale entry cleanup every 5 minutes
    """

    def __init__(self):
        self._buckets: dict[str, dict] = {}
        self._last_cleanup = time.time()
        self._cleanup_interval = 300  # 5 minutes

        # Tiered limits: (window_seconds, max_requests)
        self.tiers: dict[str, tuple[float, int]] = {
            "health": (1.0, 1000),       # health checks — very permissive
            "standard": (60.0, 30),        # read-only API
            "run": (60.0, 5),              # create runs — expensive (Chrome + LLM)
            "batch": (60.0, 2),            # batch — very expensive
            "export": (60.0, 10),          # export results
        }

        # Map URL prefix to tier
        self.path_tiers: dict[str, str] = {
            "/health": "health",
            "/ready": "health",
            "/runs": "run",
            "/batch": "batch",
            "/export": "export",
        }

    def _get_tier(self, path: str) -> str:
        """Determine rate limit tier from URL path."""
        for prefix, tier in self.path_tiers.items():
            if path.startswith(prefix):
                return tier
        return "standard"

    def _cleanup_if_needed(self) -> None:
        """Remove stale entries to prevent memory growth."""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        stale_threshold = 3600  # 1 hour without activity
        keys_to_remove = [
            key for key, bucket in self._buckets.items()
            if now - bucket.get("last_request", 0) > stale_threshold
        ]
        for key in keys_to_remove:
            del self._buckets[key]
        self._last_cleanup = now
        if keys_to_remove:
            logger.debug(f"Rate limiter cleaned up {len(keys_to_remove)} stale entries")

    def check(self, key: str, tier: str) -> tuple[bool, dict]:
        """Check if a request is allowed. Returns (allowed, headers_for_response)."""
        self._cleanup_if_needed()
        window, max_requests = self.tiers.get(tier, self.tiers["standard"])
        now = time.time()

        bucket = self._buckets.get(key)
        if bucket is None or now - bucket.get("window_start", 0) > window:
            bucket = {"window_start": now, "tokens": max_requests - 1, "last_request": now}
            self._buckets[key] = bucket
            return True, {
                "X-RateLimit-Limit": str(max_requests),
                "X-RateLimit-Remaining": str(max_requests - 1),
                "X-RateLimit-Window": str(int(window)),
            }

        if bucket["tokens"] > 0:
            bucket["tokens"] -= 1
            bucket["last_request"] = now
            remaining = bucket["tokens"]
            return True, {
                "X-RateLimit-Limit": str(max_requests),
                "X-RateLimit-Remaining": str(remaining),
                "X-RateLimit-Window": str(int(window)),
            }

        # Rate limited
        reset_in = int(window - (now - bucket["window_start"])) + 1
        return False, {
            "X-RateLimit-Limit": str(max_requests),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Window": str(int(window)),
            "Retry-After": str(reset_in),
        }


_rate_limiter = TokenBucketRateLimiter()


def _get_rate_limit_key(request: Request) -> str:
    """Build rate limit key from IP + API key (if available)."""
    client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")
    if "," in client_ip:
        client_ip = client_ip.split(",")[0].strip()

    api_key = request.headers.get("X-API-Key", "")
    if api_key and not _AUTH_DISABLED and _is_valid_api_key(api_key):
        return f"key:{api_key[:8]}:{client_ip}"
    return f"ip:{client_ip}"


# Backward compat: simple function for tests/internal use
def _check_rate_limit(client_ip: str) -> bool:
    """Legacy simple rate limit check (used by tests)."""
    allowed, _ = _rate_limiter.check(f"ip:{client_ip}", "standard")
    return allowed


# ─── Graceful Shutdown Lifecycle ────────────────────────────────────────────

_SHUTDOWN_EVENT = asyncio.Event()


def _graceful_shutdown_signal_handler(signum, frame) -> None:
    """Handle SIGTERM/SIGINT by signalling all async loops to stop cleanly."""
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    _SHUTDOWN_EVENT.set()


def _warn_if_auth_disabled() -> None:
    """Emit a WARNING when authentication is disabled.

    When ``AUTH_DISABLED=true`` all API key checks are bypassed.  Logging a
    prominent warning at startup ensures operators are aware that the service
    is running without authentication and should only be used in trusted or
    local environments.
    """
    if _AUTH_DISABLED:
        logger.warning(
            "AUTH_DISABLED is enabled — all API authentication is bypassed. "
            "Use this only in trusted/local environments."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan manager: setup signal handlers on startup, cleanup on shutdown."""
    # Startup
    signal.signal(signal.SIGTERM, _graceful_shutdown_signal_handler)
    signal.signal(signal.SIGINT, _graceful_shutdown_signal_handler)
    _warn_if_auth_disabled()
    logger.info("API server started with graceful shutdown handlers")
    yield
    # Shutdown
    logger.info("API server shutting down...")


# ─── FastAPI app ──────────────────────────────────────────────────────────

app = FastAPI(
    title="ready-ai API",
    description="Agentic browser automation for seamless documentation generation.",
    version="0.1.0",
    lifespan=lifespan,
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


# ─── API Key Middleware ───────────────────────────────────────────────────

@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """Validate X-API-Key header on all routes except health/docs."""
    if _AUTH_DISABLED:
        return await call_next(request)

    if request.method == "OPTIONS":  # CORS preflight
        return await call_next(request)

    path = request.url.path
    if path in ("/health", "/ready", "/docs", "/openapi.json", "/"):
        return await call_next(request)

    api_key = request.headers.get("X-API-Key")
    if not api_key or not _is_valid_api_key(api_key):
        return JSONResponse(
            {"detail": "Invalid or missing X-API-Key header"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    return await call_next(request)


# ─── Rate Limit Middleware ───────────────────────────────────────────────────

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Apply tiered token-bucket rate limiting to all routes with headers in response."""
    if request.url.path in ("/docs", "/openapi.json", "/"):
        return await call_next(request)

    tier = _rate_limiter._get_tier(request.url.path)
    key = _get_rate_limit_key(request)
    allowed, headers = _rate_limiter.check(key, tier)

    response = await call_next(request) if allowed else JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "detail": f"Rate limit exceeded for tier '{tier}'. Please try again later.",
            "tier": tier,
        },
    )

    # Attach rate limit headers to all responses (success or rate limited)
    for header_name, header_value in headers.items():
        response.headers[header_name] = header_value

    return response


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
async def get_run_status(run_id: str = FPath(..., pattern=r"^[A-Za-z0-9_-]+$")):
    """Poll the status of a specific run."""
    status = RunManager.get_status(run_id)
    if not status:
        raise HTTPException(status_code=404, detail="Run not found.")
    return status


@app.get("/runs/{run_id}/output")
async def get_run_output(
    background_tasks: BackgroundTasks,
    run_id: str = FPath(..., pattern=r"^[A-Za-z0-9_-]+$"),
):
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

    # Delete the transient zip after the response has been fully served.
    background_tasks.add_task(os.unlink, zip_path)

    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=f"browser_docs_{run_id}.zip",
    )


@app.get("/runs/{run_id}/metrics")
async def get_run_metrics(run_id: str = FPath(..., pattern=r"^[A-Za-z0-9_-]+$")):
    """Retrieve observability metrics for a completed run."""
    metrics_path = Path(f"./output/{run_id}_metrics.json")

    if not metrics_path.exists():
        raise HTTPException(status_code=404, detail="Metrics not found for this run.")

    data = json.loads(metrics_path.read_text())
    return JSONResponse(content=data)


# ─── Export endpoint ──────────────────────────────────────────────────────
@app.post("/runs/{run_id}/export", response_model=ExportResponse)
async def export_run(
    run_id: str = FPath(..., pattern=r"^[A-Za-z0-9_-]+$"),
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
    limit: int = Query(default=50, ge=0, le=200),
    offset: int = Query(default=0, ge=0),
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
async def get_run_diff(
    run_id: str = FPath(..., pattern=r"^[A-Za-z0-9_-]+$"),
    previous_run_id: str | None = Query(default=None, pattern=r"^[A-Za-z0-9_-]+$"),
):
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
    limit: int = Query(default=50, ge=0, le=200),
    offset: int = Query(default=0, ge=0),
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
    limit: int = Query(default=50, ge=0, le=200),
    offset: int = Query(default=0, ge=0),
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
