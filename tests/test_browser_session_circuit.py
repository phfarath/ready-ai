"""
Tests for Commit 5 of P0-1: BrowserSession hooks into the
CDP circuit breaker — the `is_disconnected` property, the
`cdp_state` diagnostic, and the structured logs emitted by
`recover()` / `teardown()`.

These tests use a fully mocked BrowserSession (no real Chrome)
because the focus is on the new public surface, not on Chrome
lifecycle. The CDPConnection is real so we can drive its FSM
directly.
"""

import asyncio
import sys
import websockets
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent import recovery
from src.agent.browser_session import BrowserSession
from src.agent.executor import StepResult
from src.agent.loop import MAX_CRASHES, AgenticLoop
from src.agent.state import RunState
from src.cdp.connection import CDPConnection
from src.cdp.connection_state import ConnectionState
from src.cdp.exceptions import CircuitOpenError, WebSocketDisconnected
from src.docs.renderer import DocRenderer


class TestIsDisconnected:
    def test_false_when_no_connection(self):
        # A freshly constructed session has no conn yet.
        bs = BrowserSession()
        assert bs.is_disconnected is False

    def test_false_when_healthy(self):
        bs = BrowserSession()
        bs._conn = CDPConnection()
        bs._conn._state = ConnectionState.HEALTHY
        assert bs.is_disconnected is False
        assert bs.cdp_state == "healthy"

    def test_false_when_degraded(self):
        bs = BrowserSession()
        bs._conn = CDPConnection()
        bs._conn._state = ConnectionState.DEGRADED
        assert bs.is_disconnected is False
        assert bs.cdp_state == "degraded"

    def test_true_when_down(self):
        bs = BrowserSession()
        bs._conn = CDPConnection()
        bs._conn._state = ConnectionState.DOWN
        assert bs.is_disconnected is True
        assert bs.cdp_state == "down"

    def test_true_when_closed(self):
        bs = BrowserSession()
        bs._conn = CDPConnection()
        bs._conn._state = ConnectionState.CLOSED
        assert bs.is_disconnected is True
        assert bs.cdp_state == "closed"

    def test_cdp_state_is_none_without_connection(self):
        bs = BrowserSession()
        assert bs.cdp_state is None


class TestTeardownMarksIntentionalClose:
    @pytest.mark.asyncio
    async def test_teardown_sets_intentional_close(self):
        # The close() method on the connection is the one that
        # actually flips the _intentional_close flag (Commit 2).
        # We just verify that teardown() reaches close() with a
        # properly wired connection.
        bs = BrowserSession()
        bs._conn = CDPConnection()
        bs._conn._state = ConnectionState.HEALTHY
        bs._conn.close = AsyncMock()
        bs._chrome_proc = None
        await bs.teardown()
        bs._conn.close.assert_called_once()
        # The mocked close() above replaces the real one, so we
        # cannot assert on _intentional_close here. The Commit 2
        # tests cover that path directly. This test just guards
        # against future regressions in the teardown wiring.

    @pytest.mark.asyncio
    async def test_teardown_emits_structured_log(self):
        from src.observability import init_run_context

        init_run_context("test-bs-teardown")
        bs = BrowserSession()
        bs._conn = CDPConnection()
        bs._conn._state = ConnectionState.DOWN  # Recovery-like teardown
        bs._conn.close = AsyncMock()
        bs._chrome_proc = None
        # We can't easily assert on log_event output, so we just
        # verify teardown completes without raising.
        await bs.teardown()


class TestRecoverLogsCircuitState:
    @pytest.mark.asyncio
    async def test_recover_logs_prior_cdp_state(self):
        from src.observability import init_run_context

        init_run_context("test-bs-recover")
        bs = BrowserSession()
        bs._conn = CDPConnection()
        bs._conn._state = ConnectionState.DOWN
        # Mock teardown/setup/navigate so we don't actually launch
        # Chrome. The point is to assert the structured logs fire.
        bs.teardown = AsyncMock()
        bs.setup = AsyncMock()

        # setup() is responsible for assigning _conn and domains.
        # We need to do that here so the test doesn't blow up.
        async def fake_setup():
            bs._page = AsyncMock()
            bs._page.enable = AsyncMock()
            bs._page.navigate = AsyncMock()
        bs.setup.side_effect = fake_setup

        with patch("src.agent.browser_session.log_event") as mock_log:
            await bs.recover("https://example.com/where-we-crashed")
            # log_event was called with browser_recover_start and
            # browser_recover_complete, both with the prior cdp_state.
            start_calls = [
                c for c in mock_log.call_args_list
                if c.args and c.args[0] == "browser_recover_start"
            ]
            complete_calls = [
                c for c in mock_log.call_args_list
                if c.args and c.args[0] == "browser_recover_complete"
            ]
            assert start_calls, "browser_recover_start not emitted"
            assert complete_calls, "browser_recover_complete not emitted"
            # The prior state was DOWN.
            for c in start_calls + complete_calls:
                assert c.kwargs.get("cdp_state") == "down" or c.kwargs.get("prior_cdp_state") == "down"


class TestSessionReconnectingSurface:
    """Session-level view of the connection's own reconnect machinery.

    READY-AI-T-3: the recovery coordinator decides between "the
    connection healed itself (reattach)" and "full respawn" based on
    these signals.
    """

    def test_false_without_connection(self):
        bs = BrowserSession()
        assert bs.is_reconnecting is False

    def test_false_when_connection_is_stable(self):
        bs = BrowserSession()
        bs._conn = CDPConnection()
        bs._conn._state = ConnectionState.HEALTHY
        assert bs.is_reconnecting is False

    @pytest.mark.asyncio
    async def test_true_while_auto_reconnect_in_flight(self):
        bs = BrowserSession()
        bs._conn = CDPConnection()
        task = asyncio.create_task(asyncio.sleep(10))
        bs._conn._reconnect_task = task
        try:
            assert bs.is_reconnecting is True
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_wait_for_reconnect_proxies_connection(self):
        bs = BrowserSession()
        bs._conn = CDPConnection()
        bs._conn._state = ConnectionState.DEGRADED

        async def heal():
            await asyncio.sleep(0.05)
            bs._conn._state = ConnectionState.HEALTHY

        asyncio.create_task(heal())
        result = await bs.wait_for_reconnect(timeout=2.0, poll_interval=0.01)
        assert result == ConnectionState.HEALTHY

    @pytest.mark.asyncio
    async def test_wait_for_reconnect_without_connection(self):
        bs = BrowserSession()
        assert await bs.wait_for_reconnect(timeout=1.0) is None


class TestResumeStepIndex:
    """Checkpoint-preservation helper used by the step loop."""

    def test_returns_last_checkpoint_index(self):
        state = RunState(run_id="r", goal="g", url="u")
        state.current_step_index = 2
        assert recovery.resume_step_index(state, step_count=5) == 2

    def test_defaults_to_zero_for_fresh_state(self):
        state = RunState(run_id="r", goal="g", url="u")
        assert recovery.resume_step_index(state, step_count=5) == 0

    def test_clamps_corrupt_overrun_to_zero(self):
        # A corrupted checkpoint that points past the plan must not
        # make the loop skip or loop past the end of the step list.
        state = RunState(run_id="r", goal="g", url="u")
        state.current_step_index = 7
        assert recovery.resume_step_index(state, step_count=3) == 0


# ---------------------------------------------------------------------------
# READY-AI-T-3: the AgenticLoop recovery coordinator
# ---------------------------------------------------------------------------

class _DisconnectPage:
    """Page stub whose get_dom_html raises once at a configured call index.

    Lets a test inject a disconnect at an exact point in the step
    loop: call 1 = step 1 setup, call 2 = step 2 setup, etc.
    """

    def __init__(self, fail_on_call: int, exc: Exception):
        self.fail_on_call = fail_on_call
        self.exc = exc
        self.calls = 0

    async def get_dom_html(self, max_length=4000):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise self.exc
        return f"<html>page-call-{self.calls}</html>"

    async def wait_for_network_idle(self, timeout=10.0, idle_time=0.5):
        return None

    async def screenshot(self):
        return "c2NyZWVuc2hvdA=="


class _OkPage:
    async def get_dom_html(self, max_length=4000):
        return "<html>ok</html>"

    async def wait_for_network_idle(self, timeout=10.0, idle_time=0.5):
        return None

    async def screenshot(self):
        return "c2NyZWVuc2hvdA=="


class _StubInput:
    pass


class _UrlRuntime:
    def __init__(self, url="https://app.local/a"):
        self.url = url

    async def evaluate(self, expression):
        assert expression == "window.location.href"
        return self.url

    async def get_interactive_elements(self):
        return "[]"


def _make_recovery_loop(tmp_path) -> tuple[AgenticLoop, BrowserSession, CDPConnection]:
    """A loop wired to a real BrowserSession/CDPConnection pair with the
    checkpoint write disabled (tests run in a /tmp sandbox)."""
    loop = AgenticLoop(
        goal="T-3 recovery",
        url="https://app.local/base",
        output_dir=str(tmp_path),
        headless=True,
    )
    loop._save_checkpoint = lambda *args, **kwargs: None
    bs = BrowserSession()
    conn = CDPConnection()
    bs._conn = conn
    loop._session = bs
    return loop, bs, conn


def _success_result() -> StepResult:
    return StepResult(
        action_desc="Clicked element: #save",
        success=True,
        retry_needed=False,
        attempts=1,
        status="completed",
    )


def _patch_loop_helpers(monkeypatch, exec_mock: AsyncMock) -> MagicMock:
    """Wire the loop's execution helpers to fakes (same pattern as
    test_agent_loop_spa_drift.py)."""
    monkeypatch.setattr("src.agent.loop.executor.execute_step", exec_mock)
    monkeypatch.setattr(
        "src.agent.loop.recovery.dom_fingerprint", AsyncMock(return_value="fp")
    )
    monkeypatch.setattr(
        "src.agent.loop.CursorAnimator.highlight_element", AsyncMock()
    )
    monkeypatch.setattr(
        "src.agent.loop.CursorAnimator.clear_highlight", AsyncMock()
    )
    annotation = MagicMock()
    annotation.complete_with_vision = AsyncMock(return_value="Annotation")
    return annotation


class TestRecoveryCoordinator:
    """The unified disconnect-recovery path of the AgenticLoop."""

    @pytest.mark.parametrize(
        "disconnect_exc",
        [
            WebSocketDisconnected("socket dropped"),
            websockets.exceptions.ConnectionClosed(None, None),
        ],
        ids=["websocket-disconnected", "connection-closed"],
    )
    @pytest.mark.asyncio
    async def test_both_disconnect_types_share_one_recovery_path(
        self, tmp_path, monkeypatch, disconnect_exc
    ):
        from src.observability import init_run_context, get_metrics

        init_run_context("test-t3-same-path")
        loop, bs, conn = _make_recovery_loop(tmp_path)
        # Circuit already open: the connection's own machinery gave up,
        # so the step loop must fall through to a full respawn.
        conn._state = ConnectionState.DOWN

        bs._page = _DisconnectPage(fail_on_call=2, exc=disconnect_exc)
        bs._input = _StubInput()
        bs._runtime = _UrlRuntime("https://app.local/a")

        recover_calls: list[str] = []

        async def fake_recover(url, llm=None):
            recover_calls.append(url)
            bs._page = _OkPage()
            bs._runtime = _UrlRuntime("https://app.local/a")

        bs.recover = fake_recover

        exec_mock = AsyncMock(return_value=_success_result())
        annotation = _patch_loop_helpers(monkeypatch, exec_mock)
        doc = DocRenderer("T-3 same path")

        results = await loop._execute_steps(
            ["S1", "S2"], MagicMock(), annotation, doc
        )

        # The disconnect went through the SAME handler for both types.
        assert get_metrics().get_counter("recovery.crash") == 1
        # Completed steps were NOT re-executed: exactly one attempt per
        # step (S2's first attempt died before the executor).
        assert exec_mock.await_count == 2
        assert len(doc.steps) == 2
        assert [s.step_number for s in doc.steps] == [1, 2]
        # The step cursor advanced past the completed steps only.
        assert loop._state.current_step_index == 2
        assert len(results) == 2
        assert all(r.success for r in results)
        # One respawn, back to the last known URL.
        assert recover_calls == ["https://app.local/a"]

    @pytest.mark.asyncio
    async def test_checkpoint_preserved_completed_steps_not_rerun(
        self, tmp_path, monkeypatch
    ):
        loop, bs, conn = _make_recovery_loop(tmp_path)
        conn._state = ConnectionState.DOWN
        # Step 3's pre-step DOM fetch raises (3rd get_dom_html call).
        bs._page = _DisconnectPage(
            fail_on_call=3, exc=WebSocketDisconnected("boom")
        )
        bs._input = _StubInput()
        bs._runtime = _UrlRuntime()

        recover_calls: list[str] = []

        async def fake_recover(url, llm=None):
            recover_calls.append(url)
            bs._page = _OkPage()

        bs.recover = fake_recover

        exec_mock = AsyncMock(return_value=_success_result())
        annotation = _patch_loop_helpers(monkeypatch, exec_mock)
        doc = DocRenderer("T-3 checkpoint")

        results = await loop._execute_steps(
            ["S1", "S2", "S3"], MagicMock(), annotation, doc
        )

        # S1 and S2 ran exactly once — the checkpointed steps were NOT
        # re-executed after the respawn (would be 4 calls otherwise).
        assert exec_mock.await_count == 3
        assert len(doc.steps) == 3
        assert [s.step_number for s in doc.steps] == [1, 2, 3]
        assert loop._state.current_step_index == 3
        assert len(recover_calls) == 1
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_successful_reattach_resumes_on_same_session(
        self, tmp_path, monkeypatch
    ):
        from src.observability import init_run_context, get_metrics

        init_run_context("test-t3-reattach")
        loop, bs, conn = _make_recovery_loop(tmp_path)
        # Auto-reconnect is in flight when the drop lands.
        conn._state = ConnectionState.DEGRADED
        conn._reconnect_task = asyncio.create_task(asyncio.sleep(300))
        page = _DisconnectPage(
            fail_on_call=2, exc=WebSocketDisconnected("drop")
        )
        bs._page = page
        bs._input = _StubInput()
        bs._runtime = _UrlRuntime()
        bs.recover = AsyncMock()  # must NOT be called on a heal

        # The connection's reconnect+reattach succeeds moments after
        # the drop lands (any time before the bounded heal wait ends).
        async def heal_in_place():
            while page.calls < 2:
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.05)
            conn._state = ConnectionState.HEALTHY

        asyncio.create_task(heal_in_place())

        exec_mock = AsyncMock(return_value=_success_result())
        annotation = _patch_loop_helpers(monkeypatch, exec_mock)
        doc = DocRenderer("T-3 reattach")

        results = await loop._execute_steps(
            ["S1", "S2"], MagicMock(), annotation, doc
        )

        assert conn._state == ConnectionState.HEALTHY
        assert get_metrics().get_counter("recovery.reattach") == 1
        # No full respawn happened.
        bs.recover.assert_not_awaited()
        # Same session/page instance was reused: call 1 (S1), call 2
        # (S2, raised), call 3 (S2 retry) — no fresh page was swapped in.
        assert page.calls == 3
        assert exec_mock.await_count == 2
        assert loop._state.current_step_index == 2
        assert [s.step_number for s in doc.steps] == [1, 2]
        assert [r.success for r in results] == [True, True]

        conn._reconnect_task.cancel()
        try:
            await conn._reconnect_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_retry_exhaustion_opens_circuit_then_respawns_once(
        self, tmp_path, monkeypatch
    ):
        loop, bs, conn = _make_recovery_loop(tmp_path)
        conn._state = ConnectionState.DEGRADED
        conn._reconnect_task = asyncio.create_task(asyncio.sleep(300))
        page = _DisconnectPage(
            fail_on_call=2, exc=WebSocketDisconnected("drop")
        )
        bs._page = page
        bs._input = _StubInput()
        bs._runtime = _UrlRuntime()

        recover_calls: list[str] = []

        async def fake_recover(url, llm=None):
            recover_calls.append(url)
            bs._page = _OkPage()

        bs.recover = fake_recover

        # The auto-reconnect exhausts its attempts shortly after the
        # drop lands: circuit opens while the loop is still waiting.
        async def exhaust_retries():
            while page.calls < 2:
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.05)
            conn._state = ConnectionState.DOWN

        asyncio.create_task(exhaust_retries())

        exec_mock = AsyncMock(return_value=_success_result())
        annotation = _patch_loop_helpers(monkeypatch, exec_mock)
        doc = DocRenderer("T-3 exhaustion")

        results = await loop._execute_steps(
            ["S1", "S2"], MagicMock(), annotation, doc
        )

        assert conn._state == ConnectionState.DOWN
        # Exactly one respawn after the circuit opened.
        assert recover_calls == ["https://app.local/a"]
        assert exec_mock.await_count == 2
        assert loop._state.current_step_index == 2
        assert len(results) == 2

        conn._reconnect_task.cancel()
        try:
            await conn._reconnect_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_open_circuit_terminates_with_structured_error(
        self, tmp_path, monkeypatch
    ):
        from src.observability import init_run_context, get_metrics

        init_run_context("test-t3-structured")
        loop, bs, conn = _make_recovery_loop(tmp_path)
        conn._state = ConnectionState.DOWN
        # The recovery budget is already spent: the next drop must NOT
        # trigger another respawn — it terminates with a structured
        # error instead of retrying forever.
        loop._connection_crashes = MAX_CRASHES
        bs._page = _DisconnectPage(
            fail_on_call=2, exc=WebSocketDisconnected("down")
        )
        bs._input = _StubInput()
        bs._runtime = _UrlRuntime()
        bs.recover = AsyncMock()

        exec_mock = AsyncMock(return_value=_success_result())
        annotation = _patch_loop_helpers(monkeypatch, exec_mock)
        doc = DocRenderer("T-3 structured")

        with pytest.raises(CircuitOpenError) as excinfo:
            await loop._execute_steps(
                ["S1", "S2"], MagicMock(), annotation, doc
            )

        # No further retry, no respawn.
        bs.recover.assert_not_awaited()
        err = excinfo.value
        assert err.state == "down"
        assert err.attempts == MAX_CRASHES + 1
        assert err.step == 1  # the step that was running at the drop
        assert get_metrics().get_counter("recovery.exhausted") == 1
        # Steps completed before the drop were checkpointed and kept.
        assert exec_mock.await_count == 1
        assert len(doc.steps) == 1

    @pytest.mark.asyncio
    async def test_recovery_budget_respawns_max_times_then_terminates(
        self, tmp_path, monkeypatch
    ):
        loop, bs, conn = _make_recovery_loop(tmp_path)
        conn._state = ConnectionState.DOWN
        # One respawn left in the budget (MAX_CRASHES - 1 already spent).
        loop._connection_crashes = MAX_CRASHES - 1
        first_page = _DisconnectPage(
            fail_on_call=2, exc=WebSocketDisconnected("first")
        )
        # After the respawn, the fresh session fails once more (S3).
        second_page = _DisconnectPage(
            fail_on_call=2, exc=WebSocketDisconnected("second")
        )
        pages = [second_page]
        bs._page = first_page
        bs._input = _StubInput()
        bs._runtime = _UrlRuntime()

        recover_calls: list[str] = []

        async def fake_recover(url, llm=None):
            recover_calls.append(url)
            bs._page = pages.pop(0)

        bs.recover = fake_recover

        exec_mock = AsyncMock(return_value=_success_result())
        annotation = _patch_loop_helpers(monkeypatch, exec_mock)
        doc = DocRenderer("T-3 budget")

        with pytest.raises(CircuitOpenError) as excinfo:
            await loop._execute_steps(
                ["S1", "S2", "S3"], MagicMock(), annotation, doc
            )

        # First drop -> respawn #MAX_CRASHES (budget met); second drop
        # -> budget exceeded -> structured terminal error.
        assert recover_calls == ["https://app.local/a"]
        assert excinfo.value.attempts == MAX_CRASHES + 1
        assert excinfo.value.state == "down"
        # S1 and S2 completed; S3 crashed before executing either time.
        assert len(recover_calls) == 1
        assert exec_mock.await_count == 2
        assert len(doc.steps) == 2
        assert loop._state.current_step_index == 2

    @pytest.mark.asyncio
    async def test_cancellation_during_recovery_is_not_swallowed(
        self, tmp_path, monkeypatch
    ):
        loop, bs, conn = _make_recovery_loop(tmp_path)
        conn._state = ConnectionState.DEGRADED
        conn._reconnect_task = asyncio.create_task(asyncio.sleep(300))
        # Fail on the very first step so the handler enters the heal
        # wait immediately.
        bs._page = _DisconnectPage(
            fail_on_call=1, exc=WebSocketDisconnected("drop")
        )
        bs._input = _StubInput()
        bs._runtime = _UrlRuntime()
        bs.recover = AsyncMock()

        exec_mock = AsyncMock(return_value=_success_result())
        annotation = _patch_loop_helpers(monkeypatch, exec_mock)
        doc = DocRenderer("T-3 cancellation")

        run_task = asyncio.create_task(
            loop._execute_steps(["S1"], MagicMock(), annotation, doc)
        )
        # The recovery is now blocked in the bounded heal wait.
        await asyncio.sleep(0.2)
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task

        # Cancellation is terminal for the task: it must NOT be masked
        # as a recovery success or a structured circuit error.
        bs.recover.assert_not_awaited()
        assert loop._state.current_step_index == 0

        conn._reconnect_task.cancel()
        try:
            await conn._reconnect_task
        except asyncio.CancelledError:
            pass


class TestReexecuteMissingSteps:
    """Sub-plan (critic) re-execution must start at its OWN step 0.

    READY-AI-T-3/Q3: `_execute_steps` previously seeded every run from
    `recovery.resume_step_index(self._state, ...)`, and that helper
    reads `state.current_step_index` — an index belonging to the MAIN
    plan. Once the main-plan cursor is nonzero, a later critic round
    would silently skip the first missing step of its sub-plan.
    """

    @pytest.mark.asyncio
    async def test_second_critic_round_executes_both_sub_steps(
        self, tmp_path, monkeypatch
    ):
        loop, bs, conn = _make_recovery_loop(tmp_path)
        bs._page = _OkPage()
        bs._input = _StubInput()
        bs._runtime = _UrlRuntime()

        llm = MagicMock()
        llm.complete = AsyncMock(
            return_value="1. Re-cover the CTA\n2. Capture the success toast"
        )

        exec_mock = AsyncMock(return_value=_success_result())
        annotation = _patch_loop_helpers(monkeypatch, exec_mock)
        doc = DocRenderer("T-3 sub-plan: 2nd round")

        # First critic round: a 2-step sub-plan executed in full. The
        # shared _execute_steps advances the MAIN-plan cursor to 2.
        results1 = await loop._reexecute_missing_steps(
            ["missing A", "missing B"], llm, annotation, doc
        )
        assert len(results1) == 2
        assert exec_mock.await_count == 2
        assert loop._state.current_step_index == 2

        # Second critic round: a fresh 2-step sub-plan. Its steps must
        # ALL run again — the nonzero main-plan cursor must NOT leak
        # into the sub-plan (pre-fix, the first of the 2 was skipped).
        results2 = await loop._reexecute_missing_steps(
            ["missing C", "missing D"], llm, annotation, doc
        )

        assert len(results2) == 2
        # 2 (round 1) + 2 (round 2) — BOTH round-2 sub-steps executed.
        assert exec_mock.await_count == 4
        # All four doc steps present with consecutive numbers — none
        # skipped.
        assert [s.step_number for s in doc.steps] == [1, 2, 3, 4]
        assert all(r.success for r in results2)
