"""
Tests for Commit 3 of P0-1: the reconnect loop itself, with
exponential backoff, jitter, the hybrid re-attach (auto-attach
event wait + manual attach fallback), and the
cdp.reconnect.attempts counter.

We patch both `websockets.connect` and `asyncio.sleep` so the
tests run in milliseconds. Patching sleep also lets us assert
the exact backoff sequence without flake.
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cdp.connection import CDPConnection
from src.cdp.connection_state import (
    RECONNECT_BASE_S,
    RECONNECT_CAP_S,
    RECONNECT_MAX_ATTEMPTS,
    ConnectionState,
)
from src.cdp.exceptions import CircuitOpenError, WebSocketDisconnected


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ws_mock() -> AsyncMock:
    """A WebSocket mock usable by the recv loop and reconnect path."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    # Make the async iterator end immediately so `async for raw in
    # self._ws` exits cleanly. The recv loop will then await the next
    # message, which never comes — exactly what we want for these
    # tests since we never drive the socket from outside.
    async def _empty_iter():
        if False:
            yield
    ws.__aiter__ = lambda self=None: _empty_iter()
    return ws


async def _resolve_pending(conn: CDPConnection) -> None:
    """Resolve every pending future with an empty result."""
    for msg_id, fut in list(conn._pending.items()):
        if not fut.done():
            fut.set_result({"id": msg_id, "result": {"ok": True}})
    conn._pending.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReconnectHappyPath:
    @pytest.mark.asyncio
    async def test_reconnect_after_one_failure(self):
        # First attempt fails, second succeeds. We expect:
        #   - one failure metric
        #   - one success metric
        #   - state back to HEALTHY
        #   - abort_wait cleared
        from src.observability import init_run_context, get_metrics

        init_run_context("test-c3-happy")

        # Real websockets.connect side effect: first call raises,
        # second returns a fresh mock WS.
        second_ws = _ws_mock()

        connect_results: list = [RuntimeError("boom"), second_ws]

        async def fake_connect(*args, **kwargs):
            res = connect_results.pop(0)
            if isinstance(res, Exception):
                raise res
            return res

        # Mock out the post-reconnect re-attach so the test focuses
        # on the loop's backoff/state machine, not on re-attach
        # details (those have their own test class).
        with patch("src.cdp.connection.websockets.connect", new=fake_connect), \
             patch("src.cdp.connection.asyncio.sleep", new=AsyncMock()) as sleep_mock, \
             patch.object(CDPConnection, "_post_reconnect_reattach", new=AsyncMock()):
            conn = CDPConnection()
            conn._ws_url = "ws://test"
            # Simulate the FSM being in DEGRADED already.
            conn._state = ConnectionState.DEGRADED
            conn._consecutive_failures = 1
            conn._first_failure_ts = 0.0

            # Spawn the reconnect directly (skips the schedule
            # path so we don't have to mock _handle_disconnect).
            await conn._reconnect()

            # State machine back to HEALTHY.
            assert conn._state == ConnectionState.HEALTHY
            # Counter reset.
            assert conn._consecutive_failures == 0
            # One sleep before the failed attempt, one before
            # the successful one.
            assert sleep_mock.await_count == 2
            # Backoff sequence: base*2^0 = RECONNECT_BASE_S,
            # then base*2^1 = 2*RECONNECT_BASE_S.
            first_delay = sleep_mock.await_args_list[0].args[0]
            second_delay = sleep_mock.await_args_list[1].args[0]
            assert first_delay >= RECONNECT_BASE_S
            assert first_delay <= RECONNECT_BASE_S * 1.1
            assert second_delay >= RECONNECT_BASE_S * 2
            assert second_delay <= RECONNECT_BASE_S * 2 * 1.1
            # Counters.
            metrics = get_metrics()
            successes = metrics.get_counter_by_attr("cdp.reconnect.attempts")
            # success
            assert (
                json.dumps(
                    {"outcome": "success"}, sort_keys=True
                )
                in successes
            )
            # failure
            assert (
                json.dumps(
                    {"outcome": "failure"}, sort_keys=True
                )
                in successes
            )


class TestReconnectExhaustion:
    @pytest.mark.asyncio
    async def test_exhaustion_opens_circuit(self):
        from src.observability import init_run_context, get_metrics

        init_run_context("test-c3-exhaust")

        async def always_fail(*args, **kwargs):
            raise RuntimeError("network down")

        with patch("src.cdp.connection.websockets.connect", new=always_fail):
            with patch("src.cdp.connection.asyncio.sleep", new=AsyncMock()):
                conn = CDPConnection()
                conn._ws_url = "ws://test"
                conn._state = ConnectionState.DEGRADED
                conn._consecutive_failures = 1

                await conn._reconnect()

                # All attempts failed.
                assert conn._state == ConnectionState.DOWN
                # Disconnect event is set so wait_disconnected() unblocks.
                assert conn._disconnect_event.is_set()
                # Exhaustion counter.
                metrics = get_metrics()
                assert metrics.get_counter("cdp.reconnect.exhausted") == 1


class TestReconnectAborted:
    @pytest.mark.asyncio
    async def test_reconnect_aborts_when_state_becomes_closed(self):
        # If the orchestrator closes the connection mid-backoff, the
        # reconnect should stop trying on the next iteration rather
        # than dial Chrome after the orchestrator gave up.
        with patch("src.cdp.connection.asyncio.sleep") as sleep_mock:
            # Make sleep also flip the state to CLOSED so the loop
            # sees the cancellation in its next iteration.
            async def close_during_sleep(_):
                conn._state = ConnectionState.CLOSED

            sleep_mock.side_effect = close_during_sleep
            conn = CDPConnection()
            conn._ws_url = "ws://test"
            conn._state = ConnectionState.DEGRADED
            await conn._reconnect()
            # We exited via the early-return; state stays CLOSED.
            assert conn._state == ConnectionState.CLOSED


class TestReconnectReattach:
    @pytest.mark.asyncio
    async def test_reattach_via_auto_attach_event(self):
        # Chrome sends Target.attachedToTarget during the wait window
        # and we pick it up via the recv loop's existing handler.
        # We simulate the recv loop's update by directly setting
        # _session_id from a background task.
        ws = _ws_mock()
        # Auto-attach event arrives 0.1s into the 3s wait.
        async def delayed_attach():
            await asyncio.sleep(0.1)
            conn._session_id = "new-session-from-autoattach"

        # The send calls in the re-enable path just need to return
        # a result dict.
        async def fake_send(method, *args, **kwargs):
            if method == "Page.enable":
                return {}
            return {}

        with patch("src.cdp.connection.asyncio.sleep", new=AsyncMock()):
            conn = CDPConnection()
            conn._ws = ws
            conn._ws_url = "ws://test"
            conn._target_id = "target-1"
            conn._state = ConnectionState.DEGRADED
            conn.send = fake_send  # type: ignore[assignment]
            with patch(
                "src.cdp.page.register_cursor_script", new=AsyncMock()
            ):
                # The auto-attach event itself doesn't need to be in
                # the queue; the recv loop would deliver it. For
                # this test we directly trigger the session_id update
                # via a concurrent task.
                asyncio.create_task(delayed_attach())
                # Run the re-attach synchronously.
                await conn._post_reconnect_reattach()

            assert conn._session_id == "new-session-from-autoattach"

    @pytest.mark.asyncio
    async def test_reattach_fallback_to_manual_attach(self):
        # No auto-attach arrives, so we fall back to
        # Target.attachToTarget with the cached target_id.
        attach_called_with: list[dict] = []

        async def fake_send(method, params=None, session_id=None, timeout=30.0):
            if method == "Target.attachToTarget":
                attach_called_with.append(params or {})
                return {"sessionId": "manual-session"}
            return {}

        with patch("src.cdp.connection.asyncio.sleep", new=AsyncMock()):
            conn = CDPConnection()
            conn._ws = _ws_mock()
            conn._ws_url = "ws://test"
            conn._target_id = "cached-target-id"
            conn._state = ConnectionState.DEGRADED
            conn.send = fake_send  # type: ignore[assignment]

            with patch(
                "src.cdp.page.register_cursor_script", new=AsyncMock()
            ):
                await conn._post_reconnect_reattach()

            assert conn._session_id == "manual-session"
            assert attach_called_with and attach_called_with[0]["targetId"] == "cached-target-id"
            # The re-enable path called Page.enable etc.
            # (we don't assert order — just that send was called)

    @pytest.mark.asyncio
    async def test_reattach_raises_when_no_session(self):
        # No auto-attach, no cached target_id → no session_id →
        # RuntimeError so the reconnect loop counts the attempt as
        # a failure.
        with patch("src.cdp.connection.asyncio.sleep", new=AsyncMock()):
            conn = CDPConnection()
            conn._ws = _ws_mock()
            conn._ws_url = "ws://test"
            # Deliberately no _target_id.
            with pytest.raises(RuntimeError, match="did not yield a session_id"):
                await conn._post_reconnect_reattach()


class TestBackoffGrowsAndCaps:
    @pytest.mark.asyncio
    async def test_backoff_doubles_and_caps(self):
        # Patch websockets.connect to always fail, capture sleep
        # delays, and assert they form a doubling sequence that
        # saturates at RECONNECT_CAP_S.
        from src.observability import init_run_context

        init_run_context("test-c3-backoff")

        async def always_fail(*args, **kwargs):
            raise RuntimeError("nope")

        with patch("src.cdp.connection.websockets.connect", new=always_fail):
            with patch("src.cdp.connection.asyncio.sleep", new=AsyncMock()) as sleep_mock:
                conn = CDPConnection()
                conn._ws_url = "ws://test"
                conn._state = ConnectionState.DEGRADED
                await conn._reconnect()

                delays = [c.args[0] for c in sleep_mock.await_args_list]
                # We expect RECONNECT_MAX_ATTEMPTS delays.
                assert len(delays) == RECONNECT_MAX_ATTEMPTS
                # Each delay is at least the expected base*2**i and
                # at most 1.1× (the jitter cap).
                for i, d in enumerate(delays):
                    expected_base = min(RECONNECT_CAP_S, RECONNECT_BASE_S * (2 ** i))
                    assert expected_base <= d <= expected_base * 1.1, (
                        f"delay {i} = {d} out of band [{expected_base}, {expected_base*1.1}]"
                    )


class TestReconnectingProperty:
    @pytest.mark.asyncio
    async def test_reconnecting_tracks_inflight_task(self):
        conn = CDPConnection()
        # No reconnect scheduled yet.
        assert conn.reconnecting is False

        task = asyncio.create_task(asyncio.sleep(10))
        conn._reconnect_task = task
        assert conn.reconnecting is True

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # Once the task finished (cancelled), no reconnect in flight.
        assert conn.reconnecting is False

    @pytest.mark.asyncio
    async def test_reconnecting_false_when_task_finished(self):
        async def done_immediately():
            return None

        conn = CDPConnection()
        task = asyncio.create_task(done_immediately())
        await task
        conn._reconnect_task = task
        assert conn.reconnecting is False


class TestReconnectTaskLifecycle:
    """READY-AI-T-3/Q2: a finished reconnect must free the task slot so
    a SECOND disconnect in the same session can schedule a fresh
    reconnect. Before the fix the completed task lingered in
    ``_reconnect_task`` and the ``is None`` guard in
    ``_handle_disconnect`` never let the headline reattach path run
    more than once per session."""

    @pytest.mark.asyncio
    async def test_second_disconnect_schedules_a_fresh_reconnect(self):
        ws = _ws_mock()

        async def fake_connect(*args, **kwargs):
            return ws

        # The reconnect scheduler in `_handle_disconnect` is gated by
        # the module-level AUTORECONNECT_ENABLED flag; patch it
        # directly to keep the test hermetic (no env/reload dance).
        with patch("src.cdp.connection.AUTORECONNECT_ENABLED", True), \
             patch("src.cdp.connection.websockets.connect", new=fake_connect), \
             patch("src.cdp.connection.asyncio.sleep", new=AsyncMock()), \
             patch.object(
                 CDPConnection, "_post_reconnect_reattach", new=AsyncMock()
             ):
            conn = CDPConnection()
            conn._ws_url = "ws://test"
            conn._state = ConnectionState.HEALTHY

            # FIRST disconnect: schedules a reconnect task and the FSM
            # moves HEALTHY -> DEGRADED.
            await conn._handle_disconnect(intentional=False)
            assert conn._reconnect_task is not None
            assert conn.reconnecting is True
            assert conn._state == ConnectionState.DEGRADED

            # The reconnect heals the socket and finishes. The finished
            # task must NOT keep pinning the slot.
            await conn._reconnect_task
            assert conn._state == ConnectionState.HEALTHY
            assert conn._reconnect_task is None
            assert conn.reconnecting is False

            # SECOND disconnect in the SAME session: must schedule a
            # BRAND NEW reconnect task (this is where the pre-fix bug
            # silently skipped — reconnecting stayed False forever).
            await conn._handle_disconnect(intentional=False)
            assert conn._reconnect_task is not None
            assert conn.reconnecting is True

            # Let it heal too so no task is left dangling.
            await conn._reconnect_task
            assert conn._reconnect_task is None
            assert conn._state == ConnectionState.HEALTHY


class TestWaitForReconnect:
    @pytest.mark.asyncio
    async def test_returns_immediately_when_not_degraded(self):
        # HEALTHY / DOWN / CLOSED are stable states: no polling needed.
        for state in (
            ConnectionState.HEALTHY,
            ConnectionState.DOWN,
            ConnectionState.CLOSED,
        ):
            conn = CDPConnection()
            conn._state = state
            start = asyncio.get_event_loop().time()
            result = await conn.wait_for_reconnect(timeout=1.0)
            elapsed = asyncio.get_event_loop().time() - start
            assert result == state
            assert elapsed < 0.05, f"should return instantly, took {elapsed}s"

    @pytest.mark.asyncio
    async def test_reports_healthy_after_successful_reattach(self):
        # The auto-reconnect task heals the socket: the wait should
        # observe the FSM flip DEGRADED -> HEALTHY.
        conn = CDPConnection()
        conn._state = ConnectionState.DEGRADED

        async def heal():
            await asyncio.sleep(0.05)
            conn._state = ConnectionState.HEALTHY

        asyncio.create_task(heal())
        result = await conn.wait_for_reconnect(timeout=2.0, poll_interval=0.01)
        assert result == ConnectionState.HEALTHY

    @pytest.mark.asyncio
    async def test_reports_down_when_reconnect_exhausted(self):
        # The auto-reconnect exhausts its attempts and opens the
        # circuit: the wait should observe DEGRADED -> DOWN.
        conn = CDPConnection()
        conn._state = ConnectionState.DEGRADED

        async def open_circuit():
            await asyncio.sleep(0.05)
            conn._state = ConnectionState.DOWN

        asyncio.create_task(open_circuit())
        result = await conn.wait_for_reconnect(timeout=2.0, poll_interval=0.01)
        assert result == ConnectionState.DOWN

    @pytest.mark.asyncio
    async def test_times_out_while_still_degraded(self):
        # The reconnect keeps churning past the deadline: the wait must
        # return the current DEGRADED state instead of hanging.
        conn = CDPConnection()
        conn._state = ConnectionState.DEGRADED
        start = asyncio.get_event_loop().time()
        result = await conn.wait_for_reconnect(timeout=0.05, poll_interval=0.01)
        elapsed = asyncio.get_event_loop().time() - start
        assert result == ConnectionState.DEGRADED
        assert 0.03 <= elapsed < 1.0, f"unexpected elapsed: {elapsed}"


class TestCircuitOpenError:
    def test_subclasses_web_socket_disconnected(self):
        # Structurally distinct while remaining catchable everywhere
        # the plain disconnect exception is caught.
        assert issubclass(CircuitOpenError, WebSocketDisconnected)
        assert issubclass(CircuitOpenError, RuntimeError)

    def test_carries_structured_fields(self):
        err = CircuitOpenError(
            "boom", state="down", attempts=4, step=2
        )
        assert str(err) == "boom"
        assert err.state == "down"
        assert err.attempts == 4
        assert err.step == 2

    @pytest.mark.asyncio
    async def test_send_raises_circuit_open_error_when_down(self):
        # Fail-fast on an open circuit yields the structured subtype.
        conn = CDPConnection()
        conn._ws = _ws_mock()
        conn._state = ConnectionState.DOWN
        conn._consecutive_failures = 5
        with pytest.raises(CircuitOpenError) as excinfo:
            await conn.send("Page.navigate", {"url": "x"})
        assert excinfo.value.state == "down"
        assert excinfo.value.attempts == 5
        # The socket was NOT touched.
        conn._ws.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_raises_plain_disconnected_when_closed(self):
        # CLOSED is an intentional teardown, not a circuit failure: it
        # must keep the plain (non-CircuitOpen) exception type.
        conn = CDPConnection()
        conn._ws = _ws_mock()
        conn._state = ConnectionState.CLOSED
        with pytest.raises(WebSocketDisconnected) as excinfo:
            await conn.send("Page.navigate", {"url": "x"})
        assert not isinstance(excinfo.value, CircuitOpenError)
