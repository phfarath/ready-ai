"""Engine-side run events + cooperative cancellation (READY-AI-T-14).

``AgenticLoop.set_event_sink`` receives ordered, plain-dict events and
``request_cancel`` stops the flow at step/action boundaries: remaining
steps report ``skipped``, the run resolves ``status="cancelled"`` and no
failure bundle is captured (cancel is operator intent, not a failure).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent import executor as executor_module  # noqa: F401  (patch target)
from src.agent.loop import AgenticLoop
from src.agent.state import RunState
from src.api.models import FlowAction, FlowSpec, FlowStepSpec
from src.cdp.exceptions import RunPolicyError


def _flow(steps, *, retries=0, run_id="evt-1"):
    return FlowSpec(
        name="evt",
        url="https://app.example.com/start",
        retries=retries,
        steps=steps,
        run_id=run_id,
    )


def _navigate_step(name="Go"):
    return FlowStepSpec(
        name=name,
        actions=[FlowAction(action="navigate", url="https://app.example.com/done")],
    )


def _make_loop(tmp_path, run_id="evt-1"):
    """AgenticLoop with a fully mocked BrowserSession (same recipe as
    test_agent_flow)."""
    loop = AgenticLoop(
        goal="run-flow",
        url="https://app.example.com/start",
        output_dir=str(tmp_path),
        run_id=run_id,
        headless=True,
    )
    session = loop._session
    session.setup = AsyncMock(return_value=None)
    session.teardown = AsyncMock(return_value=None)
    session.inject_cookies = AsyncMock(return_value=None)
    session.handle_login = AsyncMock(return_value=None)
    session.cookies_file = None
    session.username = None
    session.password = None

    page = MagicMock()
    page.enable = AsyncMock(return_value=None)
    page.navigate = AsyncMock(return_value=None)
    page.wait_for_network_idle = AsyncMock(return_value=None)

    runtime = MagicMock()
    runtime.evaluate = AsyncMock(return_value=None)
    runtime.query_selector = AsyncMock(return_value=None)

    input_domain = MagicMock()

    session._page = page
    session._runtime = runtime
    session._input = input_domain

    loop._save_checkpoint = MagicMock(return_value=None)
    return loop, page, runtime


async def test_events_emit_in_order_with_monotonic_seq(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.agent.loop.executor._dispatch_action",
        AsyncMock(return_value="Navigated to: https://app.example.com/done"),
    )
    loop, _page, _runtime = _make_loop(tmp_path)
    events: list[dict] = []
    loop.set_event_sink(events.append)

    result = await loop.run_flow(_flow([_navigate_step()]))

    assert result["status"] == "passed"
    types = [e["type"] for e in events]
    assert types == ["run_started", "step_started", "step_finished", "run_finished"]
    assert [e["seq"] for e in events] == [1, 2, 3, 4]
    assert events[1]["step_index"] == 1
    assert events[2]["status"] == "passed"
    assert events[-1]["status"] == "passed"
    assert all(e["run_id"] == "evt-1" for e in events)


async def test_retry_event_between_step_started_and_finished(tmp_path, monkeypatch):
    dispatch = AsyncMock(
        side_effect=[
            "[Failed] Element not found: #x",
            "Navigated to: https://app.example.com/done",
        ]
    )
    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)
    loop, _page, _runtime = _make_loop(tmp_path)
    events: list[dict] = []
    loop.set_event_sink(events.append)

    flow = _flow(
        [FlowStepSpec(actions=[FlowAction(action="navigate", url="https://app.example.com/done", retries=1)])],
    )
    result = await loop.run_flow(flow)

    assert result["status"] == "passed"
    types = [e["type"] for e in events]
    assert "retry" in types
    retry = next(e for e in events if e["type"] == "retry")
    assert retry["action"] == "navigate"
    assert retry["attempt"] == 2
    assert types.index("step_started") < types.index("retry") < types.index("step_finished")


async def test_sink_errors_never_break_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.agent.loop.executor._dispatch_action",
        AsyncMock(return_value="Navigated to: https://app.example.com/done"),
    )
    loop, _page, _runtime = _make_loop(tmp_path)

    def bad_sink(event):
        raise RuntimeError("telemetry blew up")

    loop.set_event_sink(bad_sink)
    result = await loop.run_flow(_flow([_navigate_step()]))
    assert result["status"] == "passed"


async def test_cancel_before_any_step_skips_everything(tmp_path, monkeypatch):
    dispatch = AsyncMock(return_value="Navigated to: https://app.example.com/done")
    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)
    loop, _page, _runtime = _make_loop(tmp_path)
    events: list[dict] = []
    loop.set_event_sink(events.append)

    loop.request_cancel()
    result = await loop.run_flow(_flow([_navigate_step("A"), _navigate_step("B")]))

    assert result["status"] == "cancelled"
    assert result["failure_reason"] == "cancelled by caller"
    assert dispatch.await_count == 0
    assert [s["status"] for s in result["steps"]] == ["skipped", "skipped"]
    assert all("cancelled" in s["failure_reason"] for s in result["steps"])
    assert loop._state.status == "CANCELLED"
    finished = [e for e in events if e["type"] == "run_finished"]
    assert finished and finished[-1]["status"] == "cancelled"


async def test_cancel_mid_run_stops_at_step_boundary(tmp_path, monkeypatch):
    loop, _page, _runtime = _make_loop(tmp_path)
    calls: list[str] = []

    async def dispatch(payload, *args, **kwargs):
        calls.append(payload.get("action"))
        # Cancel lands while step 1's action is in flight — the action
        # completes (cooperative), the next step never starts.
        loop.request_cancel()
        return "Navigated to: https://app.example.com/done"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)
    result = await loop.run_flow(
        _flow([_navigate_step("A"), _navigate_step("B"), _navigate_step("C")])
    )

    assert result["status"] == "cancelled"
    assert calls == ["navigate"]  # only step 1's action ran
    assert result["steps"][0]["status"] == "passed"
    assert [s["status"] for s in result["steps"][1:]] == ["skipped", "skipped"]
    assert result["steps"][1]["failure_reason"] == (
        "cancelled by caller; never executed"
    )


async def test_cancel_between_retry_attempts_aborts_action(tmp_path, monkeypatch):
    loop, _page, _runtime = _make_loop(tmp_path)

    async def dispatch(payload, *args, **kwargs):
        loop.request_cancel()
        return "[Failed] Element not found: #x"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)
    flow = _flow(
        [
            FlowStepSpec(
                actions=[
                    FlowAction(
                        action="navigate",
                        url="https://app.example.com/done",
                        retries=3,
                    )
                ]
            ),
            _navigate_step("B"),
        ]
    )
    result = await loop.run_flow(flow)

    assert result["status"] == "cancelled"
    step1 = result["steps"][0]
    # The failed action stopped retrying once cancel was requested.
    assert step1["actions"][0]["attempts"] == 1
    assert step1["actions"][0]["failure_reason"] == "cancelled by caller"
    assert result["steps"][1]["status"] == "skipped"


async def test_checkpoint_events_on_pause_and_resume(tmp_path, monkeypatch):
    human = {
        "action": "await_human",
        "reason": "Finish SSO",
        "resume_when": {"selector_visible": "#authed"},
    }
    flow = _flow(
        [
            _navigate_step("Land"),
            FlowStepSpec(name="Human SSO", actions=[FlowAction(**human)]),
            _navigate_step("Verify"),
        ]
    )
    monkeypatch.setattr(
        "src.agent.loop.executor._dispatch_action",
        AsyncMock(return_value="Navigated to: https://app.example.com/done"),
    )
    loop, _page, _runtime = _make_loop(tmp_path, run_id="sso-run")
    events: list[dict] = []
    loop.set_event_sink(events.append)

    # First run pauses at the human checkpoint (real checkpoint on disk).
    def save_paused(status=None):
        if status:
            loop._state.status = status
        RunState.to_file(loop._state, loop._state_path)

    loop._save_checkpoint = save_paused
    result = await loop.run_flow(flow)
    assert result["status"] == "paused"
    assert "checkpoint_paused" in [e["type"] for e in events]
    paused = next(e for e in events if e["type"] == "checkpoint_paused")
    assert paused["step_index"] == 2
    assert paused["reason"] == "Finish SSO"

    # Resume with a fresh loop bound to the same checkpoint file.
    loop2, _p2, _r2 = _make_loop(tmp_path, run_id="sso-run")
    loop2.resume_from = str(loop._state_path)
    loop2._state = RunState.from_file(loop._state_path)
    loop2._check_resume_condition = AsyncMock(return_value=(True, ""))
    events2: list[dict] = []
    loop2.set_event_sink(events2.append)

    monkeypatch.setattr(
        "src.agent.loop.executor._dispatch_action",
        AsyncMock(return_value="Navigated to: https://app.example.com/done"),
    )
    result2 = await loop2.run_flow(flow)

    assert result2["status"] == "passed"
    types2 = [e["type"] for e in events2]
    assert "checkpoint_resumed" in types2
    resumed = next(e for e in events2 if e["type"] == "checkpoint_resumed")
    assert resumed["step_index"] == 2


async def test_paused_checkpoint_of_foreign_run_id_is_policy_error(tmp_path):
    loop, _page, _runtime = _make_loop(tmp_path, run_id="mine")
    loop._state.status = "PAUSED"
    loop._state.run_id = "someone-else"
    loop._state.current_step_index = 1

    with pytest.raises(RunPolicyError) as excinfo:
        await loop.run_flow(_flow([_navigate_step()]))
    assert isinstance(excinfo.value, ValueError)


async def test_paused_checkpoint_out_of_range_index_is_policy_error(tmp_path):
    loop, _page, _runtime = _make_loop(tmp_path, run_id="mine")
    loop._state.status = "PAUSED"
    loop._state.current_step_index = 9

    with pytest.raises(RunPolicyError, match="outside the declared"):
        await loop.run_flow(_flow([_navigate_step()]))
