"""TDD RED: completing a flow run must append to the history store.

GET /history and /history/aggregates exist but always return empty
because record_run is never called. The completion path
(_save_flow_metrics) must also record the run so the API serves
real data.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.loop import AgenticLoop


def _new_loop(tmp_path) -> AgenticLoop:
    loop = AgenticLoop(
        goal="history proof",
        url="https://example.com",
        output_dir=str(tmp_path),
        headless=True,
        run_id="flow-1",
    )
    loop._save_checkpoint = lambda *a, **k: None  # type: ignore[method-assign]
    return loop


def _fake_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.run_summary.return_value = {
        "event": "run_complete",
        "run_id": "flow-1",
        "status": "PASSED",
        "duration_s": 12.5,
        "llm_calls": 0,
        "llm_tokens": {"prompt": 0, "completion": 0},
        "steps_executed": 2,
        "steps_succeeded": 2,
        "steps_failed": 0,
        "recovery_events": {"spa_drift": 0, "local_recovery": 0, "crash": 0},
    }
    return ctx


def test_save_flow_metrics_records_history(tmp_path, monkeypatch):
    """Completing a flow run appends a RunMetrics entry to the history store."""
    import src.history as history_mod

    monkeypatch.setattr(history_mod, "_HISTORY_DB", tmp_path / ".history.db")
    monkeypatch.chdir(tmp_path)

    loop = _new_loop(tmp_path)
    loop._save_flow_metrics(_fake_ctx(), "proof-flow", "passed", [])

    records = history_mod.get_history()
    assert len(records) == 1
    rec = records[0]
    assert rec.run_id == "flow-1"
    assert rec.status == "passed"
    assert rec.steps_count == 2
    assert rec.duration_sec == 12.5


def test_aggregates_count_lowercase_flow_statuses(tmp_path, monkeypatch):
    """get_aggregates must count the lowercase statuses the engine emits.

    run_flow reports "passed"/"failed" (lowercase); the aggregator only
    matched "PASSED"/"completed"/"success", so pass_rate was always 0
    for flow runs.
    """
    import src.history as history_mod
    from src.history import RunMetrics, record_run

    monkeypatch.setattr(history_mod, "_HISTORY_DB", tmp_path / ".history.db")
    for rid, status in (("r1", "passed"), ("r2", "failed")):
        record_run(
            RunMetrics(
                run_id=rid,
                goal="g",
                url="https://example.com",
                status=status,
                steps_count=1,
            )
        )

    agg = history_mod.get_aggregates()
    assert agg["total_runs"] == 2
    assert agg["pass_rate"] == 0.5
    assert agg["broken_rate"] == 0.5
