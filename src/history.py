"""
Historical Tracking — persists per-run metrics across sessions.

Stores metrics as JSON Lines in ./output/.history.jsonl.
Provides query interface for dashboards and trend analysis.

Usage:
    from src.history import record_run, get_history
    record_run(run_id="abc", metrics={"steps": 5, "tokens": 1200, "duration_sec": 45})
    recent = get_history(limit=10)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_HISTORY_FILE = Path("./output/.history.jsonl")


@dataclass
class RunMetrics:
    """Metrics captured for a single documentation run."""
    run_id: str
    goal: str
    url: str
    status: str
    steps_count: int = 0
    steps_passed: int = 0
    steps_broken: int = 0
    drift_detected: int = 0
    auto_healed: int = 0
    llm_tokens: int = 0
    duration_sec: float = 0.0
    app_version: Optional[str] = None
    git_commit: Optional[str] = None
    deployed_at: Optional[str] = None
    timestamp: str = field(default_factory=lambda: _now_iso())


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _ensure_history_dir() -> None:
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)


def record_run(metrics: RunMetrics) -> None:
    """Append a run's metrics to the history file."""
    _ensure_history_dir()
    line = json.dumps(asdict(metrics), ensure_ascii=False)
    with _HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    logger.info(f"Recorded history for run {metrics.run_id}")


def get_history(
    limit: int = 100,
    offset: int = 0,
    app_version: Optional[str] = None,
    status: Optional[str] = None,
) -> list[RunMetrics]:
    """Retrieve historical run metrics with optional filtering."""
    if not _HISTORY_FILE.exists():
        return []

    results: list[RunMetrics] = []
    with _HISTORY_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if app_version and data.get("app_version") != app_version:
                    continue
                if status and data.get("status") != status:
                    continue
                results.append(RunMetrics(**data))
            except (json.JSONDecodeError, TypeError):
                continue

    # Sort by timestamp descending, then paginate
    results.sort(key=lambda m: m.timestamp, reverse=True)
    return results[offset:offset + limit]


def get_aggregates(app_version: Optional[str] = None) -> dict:
    """Compute aggregate statistics over historical runs."""
    history = get_history(limit=10000, app_version=app_version)

    if not history:
        return {"total_runs": 0}

    total = len(history)
    passed = sum(1 for m in history if m.status in ("PASSED", "completed", "success"))
    broken = sum(1 for m in history if m.status in ("BROKEN", "failed"))
    drifted = sum(m.drift_detected for m in history)
    auto_healed = sum(m.auto_healed for m in history)
    total_steps = sum(m.steps_count for m in history)
    total_tokens = sum(m.llm_tokens for m in history)
    total_duration = sum(m.duration_sec for m in history)

    return {
        "total_runs": total,
        "pass_rate": round(passed / total, 3) if total else 0,
        "broken_rate": round(broken / total, 3) if total else 0,
        "drift_events": drifted,
        "auto_healed": auto_healed,
        "total_steps": total_steps,
        "avg_steps_per_run": round(total_steps / total, 1) if total else 0,
        "total_tokens": total_tokens,
        "avg_tokens_per_run": round(total_tokens / total, 0) if total else 0,
        "total_duration_sec": round(total_duration, 1),
        "avg_duration_sec": round(total_duration / total, 1) if total else 0,
    }


def get_version_history(app_version: str, limit: int = 50) -> list[RunMetrics]:
    """Get all historical runs for a specific app version."""
    return get_history(limit=limit, app_version=app_version)
