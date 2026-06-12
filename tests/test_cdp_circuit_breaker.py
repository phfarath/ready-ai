"""
Tests for Commit 4 of P0-1: circuit breaker logic on top of the
reconnect loop. The CB opens when CB_THRESHOLD consecutive
disconnects land inside CB_WINDOW_S. The CB also exposes
is_disconnected / wait_disconnected to the orchestrator.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cdp.connection import CDPConnection
from src.cdp.connection_state import CB_THRESHOLD, ConnectionState
from src.cdp.exceptions import WebSocketDisconnected


def _ws_mock() -> AsyncMock:
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()

    async def _empty_iter():
        if False:
            yield

    ws.__aiter__ = lambda self=None: _empty_iter()
    return ws


class TestCircuitBreakerOpens:
    @pytest.mark.asyncio
    async def test_circuit_opens_after_threshold_failures(self):
        from src.observability import init_run_context, get_metrics

        init_run_context("test-cb-opens")
        conn = CDPConnection()
        conn._ws = _ws_mock()
        for _ in range(CB_THRESHOLD):
            await conn._handle_disconnect(intentional=False)
        # After CB_THRESHOLD consecutive failures inside the window,
        # the circuit is OPEN.
        assert conn._state == ConnectionState.DOWN
        # The metric fired exactly once.
        assert get_metrics().get_counter("cdp.circuit.opens") == 1

    @pytest.mark.asyncio
    async def test_single_failure_keeps_circuit_closed(self):
        from src.observability import init_run_context, get_metrics

        init_run_context("test-cb-single")
        conn = CDPConnection()
        conn._ws = _ws_mock()
        await conn._handle_disconnect(intentional=False)
        assert conn._state == ConnectionState.DEGRADED
        # No circuit open yet.
        assert get_metrics().get_counter("cdp.circuit.opens") == 0

    @pytest.mark.asyncio
    async def test_reconnect_succeed_resets_counter(self):
        # After a few failures, a successful reconnect should reset
        # the counter. We simulate that path by manually setting
        # _state back to HEALTHY (the way _reconnect does internally).
        from src.observability import init_run_context, get_metrics

        init_run_context("test-cb-reset")
        conn = CDPConnection()
        conn._ws = _ws_mock()
        # Two failures (one short of the threshold).
        await conn._handle_disconnect(intentional=False)
        await conn._handle_disconnect(intentional=False)
        assert conn._consecutive_failures == 2
        # Reconnect "succeeds" — _reconnect() resets the counter.
        conn._consecutive_failures = 0
        conn._first_failure_ts = None
        conn._state = ConnectionState.HEALTHY
        # One more failure: counter starts at 1, not 3.
        await conn._handle_disconnect(intentional=False)
        assert conn._consecutive_failures == 1
        assert conn._state == ConnectionState.DEGRADED
        assert get_metrics().get_counter("cdp.circuit.opens") == 0


class TestSlidingWindow:
    @pytest.mark.asyncio
    async def test_old_failures_do_not_count(self):
        # We have one failure 100s ago, then a new one. The window
        # expired so the counter should be 1 (just the new one),
        # not 2.
        from src.observability import init_run_context

        init_run_context("test-cb-window")
        conn = CDPConnection()
        conn._ws = _ws_mock()
        # First failure: counter = 1, ts = now.
        await conn._handle_disconnect(intentional=False)
        assert conn._consecutive_failures == 1
        # Push the first failure out of the window by mutating the
        # timestamp; in production the wall clock would do this.
        import time

        conn._first_failure_ts = time.monotonic() - 100.0
        # Second failure: window expired, counter resets to 1.
        await conn._handle_disconnect(intentional=False)
        assert conn._consecutive_failures == 1


class TestIsDisconnected:
    @pytest.mark.asyncio
    async def test_is_disconnected_in_terminal_states(self):
        conn = CDPConnection()
        conn._ws = _ws_mock()
        for state, expected in [
            (ConnectionState.HEALTHY, False),
            (ConnectionState.DEGRADED, False),
            (ConnectionState.DOWN, True),
            (ConnectionState.CLOSED, True),
        ]:
            conn._state = state
            assert conn.is_disconnected is expected, f"state={state}"


class TestWaitDisconnected:
    @pytest.mark.asyncio
    async def test_returns_immediately_if_already_open(self):
        conn = CDPConnection()
        conn._ws = _ws_mock()
        conn._state = ConnectionState.DOWN
        conn._disconnect_event.set()
        # Should return in < 10ms.
        await asyncio.wait_for(conn.wait_disconnected(timeout=1.0), timeout=0.1)

    @pytest.mark.asyncio
    async def test_waits_for_event_then_returns(self):
        conn = CDPConnection()
        conn._ws = _ws_mock()
        conn._state = ConnectionState.HEALTHY

        async def open_circuit():
            await asyncio.sleep(0.05)
            conn._disconnect_event.set()
            conn._state = ConnectionState.DOWN

        asyncio.create_task(open_circuit())
        await asyncio.wait_for(conn.wait_disconnected(timeout=1.0), timeout=0.5)

    @pytest.mark.asyncio
    async def test_times_out_when_circuit_never_opens(self):
        conn = CDPConnection()
        conn._ws = _ws_mock()
        conn._state = ConnectionState.HEALTHY
        # wait_disconnected with a short timeout must return cleanly
        # via TimeoutError caught internally.
        start = asyncio.get_event_loop().time()
        await conn.wait_disconnected(timeout=0.1)
        elapsed = asyncio.get_event_loop().time() - start
        assert 0.08 <= elapsed <= 0.5, f"unexpected elapsed: {elapsed}"


class TestSendFailsFastWhenCircuitOpen:
    @pytest.mark.asyncio
    async def test_send_raises_when_circuit_just_opened(self):
        from src.observability import init_run_context

        init_run_context("test-cb-send")
        conn = CDPConnection()
        conn._ws = _ws_mock()
        for _ in range(CB_THRESHOLD):
            await conn._handle_disconnect(intentional=False)
        # Now the circuit is open. A new send should raise
        # WebSocketDisconnected in < 10ms, not wait on the WS.
        start = asyncio.get_event_loop().time()
        with pytest.raises(WebSocketDisconnected):
            await conn.send("Page.navigate", {"url": "x"})
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 0.05, f"send should be fail-fast, took {elapsed}s"
