"""History store SQLite (WAL) — TDD RED.

Mesma API publica do JSONL (record_run/get_history/get_aggregates/
get_version_history), backend sqlite3 + migração 1x do legado.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.history as history_mod
from src.history import RunMetrics, get_aggregates, get_history, record_run


def _isolate(monkeypatch, tmp_path: Path) -> Path:
    db = tmp_path / "history.db"
    monkeypatch.setenv("READY_AI_HISTORY_DB", str(db))
    monkeypatch.delenv("READY_AI_LEGACY_JSONL", raising=False)
    return db


def _metrics(run_id: str, **kwargs) -> RunMetrics:
    base = dict(
        run_id=run_id, goal="g", url="https://x.test", status="passed",
        steps_count=2, llm_tokens=10, duration_sec=1.5,
        app_version="1.0", timestamp=f"2026-10-20T00:00:{int(run_id[-2:]):02d}+00:00",
    )
    base.update(kwargs)
    return RunMetrics(**base)


def test_record_and_get_roundtrip(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    record_run(_metrics("run-01"))
    record_run(_metrics("run-02"))
    rows = get_history()
    assert [r.run_id for r in rows] == ["run-02", "run-01"]
    assert rows[0].steps_count == 2
    assert rows[0].llm_tokens == 10


def test_filters_and_pagination(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    record_run(_metrics("run-01", app_version="1.0", status="passed"))
    record_run(_metrics("run-02", app_version="2.0", status="failed"))
    record_run(_metrics("run-03", app_version="1.0", status="failed"))
    assert {r.run_id for r in get_history(app_version="1.0")} == {"run-01", "run-03"}
    assert {r.run_id for r in get_history(status="failed")} == {"run-02", "run-03"}
    page1 = get_history(limit=2)
    page2 = get_history(limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 1
    assert page1[0].run_id == "run-03"


def test_aggregates_math(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    record_run(_metrics("run-01", status="passed", steps_count=4, llm_tokens=100, duration_sec=10.0))
    record_run(_metrics("run-02", status="failed", steps_count=2, llm_tokens=50, duration_sec=5.0))
    agg = get_aggregates()
    assert agg["total_runs"] == 2
    assert agg["pass_rate"] == 0.5
    assert agg["broken_rate"] == 0.5
    assert agg["total_steps"] == 6
    assert agg["total_tokens"] == 150


def test_empty_store(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert get_history() == []
    assert get_aggregates() == {"total_runs": 0}


def test_env_override_selects_db_file(monkeypatch, tmp_path):
    custom = tmp_path / "custom.db"
    monkeypatch.setenv("READY_AI_HISTORY_DB", str(custom))
    record_run(_metrics("run-01"))
    assert custom.is_file()
    assert get_history()[0].run_id == "run-01"


def test_wal_mode(monkeypatch, tmp_path):
    db = _isolate(monkeypatch, tmp_path)
    record_run(_metrics("run-01"))
    with sqlite3.connect(str(db)) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_migration_imports_legacy_once(monkeypatch, tmp_path):
    from src.history import get_version_history

    db = tmp_path / "history.db"
    legacy = tmp_path / ".history.jsonl"
    legacy.write_text(
        "\n".join(
            json.dumps(
                {
                    "run_id": f"old-{i}", "goal": "g", "url": "u",
                    "status": "passed", "timestamp": f"2026-01-01T00:00:0{i}+00:00",
                }
            )
            for i in range(2)
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(history_mod, "_HISTORY_DB", db)
    monkeypatch.setattr(history_mod, "_LEGACY_JSONL", legacy)
    monkeypatch.delenv("READY_AI_HISTORY_DB", raising=False)
    assert {r.run_id for r in get_history()} == {"old-0", "old-1"}
    assert {r.run_id for r in get_version_history("9.9")} == set()
    # Segunda leitura nao duplica; legado foi arquivado.
    assert len(get_history(limit=100)) == 2
    assert not legacy.exists()
    assert legacy.with_suffix(".jsonl.migrated").exists()
