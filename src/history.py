"""
Historical Tracking — persists per-run metrics across sessions.

SQLite backend (WAL mode) at ./output/.history.db, overrideable via
READY_AI_HISTORY_DB. Same public API as the legacy JSONL store:
RunMetrics, record_run, get_history, get_aggregates,
get_version_history. First connection migrates a legacy
./output/.history.jsonl once (then renames it to .migrated).

Usage:
    from src.history import record_run, get_history
    record_run(RunMetrics(run_id="abc", goal="g", url="u", status="passed"))
    recent = get_history(limit=10)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_HISTORY_DB = Path("./output/.history.db")
_LEGACY_JSONL = Path("./output/.history.jsonl")
_HISTORY_DB_ENV = "READY_AI_HISTORY_DB"

_COLUMNS = (
    "run_id", "goal", "url", "status", "steps_count", "steps_passed",
    "steps_broken", "drift_detected", "auto_healed", "llm_tokens",
    "duration_sec", "app_version", "git_commit", "deployed_at", "timestamp",
)


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


def _db_path() -> Path:
    """Resolve the DB file: env override wins, else the module default.

    Both are read at call time so tests can monkeypatch either.
    """
    override = os.getenv(_HISTORY_DB_ENV)
    return Path(override) if override else _HISTORY_DB


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS runs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "run_id TEXT, goal TEXT, url TEXT, status TEXT, "
        "steps_count INTEGER, steps_passed INTEGER, steps_broken INTEGER, "
        "drift_detected INTEGER, auto_healed INTEGER, llm_tokens INTEGER, "
        "duration_sec REAL, app_version TEXT, git_commit TEXT, "
        "deployed_at TEXT, timestamp TEXT)"
    )
    _migrate_legacy_once(conn)
    return conn


def _migrate_legacy_once(conn: sqlite3.Connection) -> None:
    """Import the legacy JSONL exactly once, then archive it.

    Runs only when the table is empty and the legacy file exists —
    a second call is a no-op and the rename prevents re-imports.
    """
    legacy = _LEGACY_JSONL
    if not legacy.exists():
        return
    count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    if count:
        return
    imported = 0
    with legacy.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            row = {key: data.get(key) for key in _COLUMNS}
            if not row.get("run_id"):
                continue
            placeholders = ", ".join(["?"] * len(_COLUMNS))
            conn.execute(
                f"INSERT INTO runs ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
                [row[key] for key in _COLUMNS],
            )
            imported += 1
    conn.commit()
    archived = legacy.with_suffix(".jsonl.migrated")
    legacy.rename(archived)
    logger.info(f"Migrated {imported} legacy history rows to {_db_path()}")


def record_run(metrics: RunMetrics) -> None:
    """Append a run's metrics to the history store."""
    data = asdict(metrics)
    with _connect() as conn:
        placeholders = ", ".join(["?"] * len(_COLUMNS))
        conn.execute(
            f"INSERT INTO runs ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
            [data.get(key) for key in _COLUMNS],
        )
        conn.commit()
    logger.info(f"Recorded history for run {metrics.run_id}")


def get_history(
    limit: int = 100,
    offset: int = 0,
    app_version: Optional[str] = None,
    status: Optional[str] = None,
) -> list[RunMetrics]:
    """Retrieve historical run metrics with optional filtering.

    Newest first (timestamp DESC, insertion order as tiebreak —
    same as the legacy stable sort). Paginated via limit/offset.
    """
    with _connect() as conn:
        query = f"SELECT {', '.join(_COLUMNS)} FROM runs"
        clauses: list[str] = []
        params: list = []
        if app_version:
            clauses.append("app_version = ?")
            params.append(app_version)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        # id ASC tiebreak mirrors the legacy stable sort (file order kept
        # for equal timestamps); newest timestamps first overall.
        query += " ORDER BY timestamp DESC, id ASC LIMIT ? OFFSET ?"
        params += [limit, offset]
        rows = conn.execute(query, params).fetchall()
    return [RunMetrics(**{key: row[key] for key in _COLUMNS}) for row in rows]


def get_aggregates(app_version: Optional[str] = None) -> dict:
    """Compute aggregate statistics over historical runs."""
    history = get_history(limit=10000, app_version=app_version)

    if not history:
        return {"total_runs": 0}

    total = len(history)
    passed = sum(1 for m in history if m.status.lower() in ("passed", "completed", "success", "finished"))
    broken = sum(1 for m in history if m.status.lower() in ("broken", "failed"))
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
