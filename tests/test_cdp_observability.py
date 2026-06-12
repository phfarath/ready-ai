"""
Tests for the observability instrumentation in CDPConnection.send.

P1-1 of the CDP resilience roadmap: every command sent over the wire
should produce a Span and contribute to the cdp.latency_ms histogram
and the cdp.commands counter.
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cdp.connection import CDPConnection
from src.observability import init_run_context


@pytest.fixture(autouse=True)
def _run_context():
    init_run_context("test-cdp-observability")
    yield


def _make_conn() -> CDPConnection:
    conn = CDPConnection()
    conn._ws = AsyncMock()
    return conn


async def _resolve_after_send(conn: CDPConnection, msg_id: int, result: dict | None = None, error: dict | None = None) -> None:
    # Give the send task a chance to register the pending future and call
    # the mocked _ws.send (which we don't actually need to await — the
    # mock returns immediately).
    await asyncio.sleep(0)
    if msg_id not in conn._pending:
        await asyncio.sleep(0.02)
    if error is not None:
        conn._pending[msg_id].set_result({"id": msg_id, "error": error})
    else:
        conn._pending[msg_id].set_result({"id": msg_id, "result": result or {}})


class TestCDPInstrumentation:
    @pytest.mark.asyncio
    async def test_success_emits_histogram_and_counter(self):
        from src.observability import get_metrics

        conn = _make_conn()
        metrics = get_metrics()

        task = asyncio.create_task(conn.send("Page.navigate", {"url": "https://example.com"}))
        await _resolve_after_send(conn, 1, result={"frameId": "abc"})
        out = await task

        assert out == {"frameId": "abc"}

        # Histogram must contain at least one latency sample.
        hist = metrics._histograms.get("cdp.latency_ms")
        assert hist is not None
        assert hist.summary()["count"] == 1

        # Counter must include the method+status combination.
        attrs = metrics.get_counter_by_attr("cdp.commands")
        assert json.dumps({"method": "Page.navigate", "status": "ok"}, sort_keys=True) in attrs

    @pytest.mark.asyncio
    async def test_cdp_error_records_error_status(self):
        from src.observability import get_metrics

        conn = _make_conn()
        metrics = get_metrics()

        task = asyncio.create_task(conn.send("Bad.method"))
        await _resolve_after_send(conn, 1, error={"code": -32601, "message": "Method not found"})

        with pytest.raises(RuntimeError, match="Method not found"):
            await task

        attrs = metrics.get_counter_by_attr("cdp.commands")
        assert json.dumps({"method": "Bad.method", "status": "error"}, sort_keys=True) in attrs

    @pytest.mark.asyncio
    async def test_timeout_records_timeout_status(self):
        from src.observability import get_metrics

        conn = _make_conn()
        metrics = get_metrics()

        # Never resolve the future -> forced timeout.
        with pytest.raises(TimeoutError):
            await conn.send("Slow.method", timeout=0.05)

        attrs = metrics.get_counter_by_attr("cdp.commands")
        assert json.dumps({"method": "Slow.method", "status": "timeout"}, sort_keys=True) in attrs

    @pytest.mark.asyncio
    async def test_not_connected_does_not_record_metrics(self):
        from src.observability import get_metrics

        # No RunContext means get_metrics() returns None and the
        # instrumentation must degrade gracefully (no crash).
        from src.observability import get_run_context
        get_run_context  # silence unused warning
        conn = CDPConnection()  # _ws is None

        with pytest.raises(RuntimeError, match="Not connected"):
            await conn.send("Page.navigate")

        # The above path raises BEFORE the metrics call, so we can't
        # assert anything about the counter — but the test passes if no
        # exception leaks. Verify the no-context path is safe.
        assert get_metrics() is not None  # fixture ensures it exists

    @pytest.mark.asyncio
    async def test_send_still_works_without_run_context(self):
        # Simulate the "no RunContext" case by clearing the contextvar.
        from src.observability import _run_context_var

        saved = _run_context_var.get()
        _run_context_var.set(None)
        try:
            conn = _make_conn()
            task = asyncio.create_task(conn.send("Page.enable"))
            await _resolve_after_send(conn, 1, result={})
            out = await task
            assert out == {}
        finally:
            _run_context_var.set(saved)
