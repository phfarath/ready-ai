"""CDP-disconnect propagation regression tests (READY-AI-T-3-FOLLOW-1).

Two complementary layers:

1. Executor level — the REAL ``executor._dispatch_action`` must re-raise
   ``WebSocketDisconnected``, ``CircuitOpenError`` and
   ``websockets.exceptions.ConnectionClosed`` raised mid click/type,
   instead of swallowing them into a serializable ``"[Error] <action>: ..."
   `` string (which would trigger pointless flow retries).

2. Loop level — once the exception escapes the executor, the agent loop's
   unified handler (``except (ConnectionClosed, WebSocketDisconnected)``)
   must consume NO retry budget and surface the "CDP connection lost"
   failure reason, aborting the run truthfully.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import websockets

from src.agent.executor import _dispatch_action
from src.agent.loop import AgenticLoop
from src.api.models import FlowAction, FlowSpec, FlowStepSpec
from src.cdp.exceptions import CircuitOpenError, WebSocketDisconnected

# ─── Helpers ─────────────────────────────────────────────────────────────


def _disconnect_factories():
    """Every exception flavor the unified recovery path must recognize.

    ``CircuitOpenError`` is a ``WebSocketDisconnected`` subclass, but both
    are covered explicitly so a future refactor of the hierarchy cannot
    silently drop coverage of either identity.
    """
    return [
        pytest.param(
            lambda: WebSocketDisconnected("socket went away mid-action"),
            id="websocket_disconnected",
        ),
        pytest.param(
            lambda: CircuitOpenError(
                "circuit breaker open", state="down", attempts=4
            ),
            id="circuit_open_error",
        ),
        pytest.param(
            lambda: websockets.exceptions.ConnectionClosed(None, None),
            id="connection_closed",
        ),
    ]


def _make_domains():
    """AsyncMock page/input/runtime trio, as in the redaction tests."""
    page = AsyncMock()
    input_domain = AsyncMock()
    runtime = AsyncMock()
    return page, input_domain, runtime


def _flow(steps, *, retries=1):
    return FlowSpec(
        name="checkout",
        url="https://app.example.com/start",
        retries=retries,
        steps=steps,
    )


def _make_loop(tmp_path, run_id="disconnect-test"):
    """AgenticLoop with a fully mocked BrowserSession (test_agent_flow style)."""
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
    runtime.get_element_text = AsyncMock(return_value="")
    runtime.get_visible_text = AsyncMock(return_value="")
    runtime.get_element_attributes = AsyncMock(return_value={})

    session._page = page
    session._runtime = runtime
    session._input = MagicMock()

    loop._save_checkpoint = MagicMock(return_value=None)
    return loop, page, runtime, session


# ─── 1. Executor level: the REAL _dispatch_action re-raises ──────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_factory", _disconnect_factories())
async def test_dispatch_click_propagates_cdp_disconnection(exc_factory):
    """A CDP disconnect mid-click escapes _dispatch_action un-sanitized.

    A successful ``pytest.raises`` already proves no ``"[Error]"`` string
    was returned; the identity assertion additionally rules out any
    wrapping/replacement of the original exception object.
    """
    page, input_domain, runtime = _make_domains()
    exc = exc_factory()
    input_domain.click.side_effect = exc

    with pytest.raises(type(exc)) as excinfo:
        await _dispatch_action(
            {"action": "click", "selector": "#submit"},
            page,
            input_domain,
            runtime,
        )

    assert excinfo.value is exc


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_factory", _disconnect_factories())
async def test_dispatch_type_propagates_cdp_disconnection(exc_factory):
    """A CDP disconnect mid-type escapes _dispatch_action un-sanitized."""
    page, input_domain, runtime = _make_domains()
    exc = exc_factory()
    input_domain.type_text.side_effect = exc

    with pytest.raises(type(exc)) as excinfo:
        await _dispatch_action(
            {"action": "type", "selector": "#email", "text": "hunter2"},
            page,
            input_domain,
            runtime,
        )

    assert excinfo.value is exc
    # The disconnect fired inside the CDP call itself — nothing downstream
    # (sensitivity probe) ran, so no partial description was produced.
    runtime.evaluate.assert_not_called()


def test_circuit_open_error_is_websocket_disconnected_subclass():
    """Documents why one except-clause covers both disconnect identities."""
    assert issubclass(CircuitOpenError, WebSocketDisconnected)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        {"action": "click", "selector": "#submit"},
        {"action": "type", "selector": "#email", "text": "hunter2"},
    ],
    ids=["click", "type"],
)
async def test_dispatch_still_sanitizes_non_disconnection_errors(action):
    """Guard: ordinary errors KEEP the serializable ``[Error]`` semantics."""
    page, input_domain, runtime = _make_domains()
    target = (
        input_domain.click
        if action["action"] == "click"
        else input_domain.type_text
    )
    target.side_effect = ValueError("boom")

    result = await _dispatch_action(action, page, input_domain, runtime)

    assert result == f"[Error] {action['action']}: boom"


# ─── 2. Loop level: unified recovery consumes no retry budget ────────────


@pytest.mark.asyncio
async def test_run_flow_click_disconnect_consumes_no_retry(tmp_path, monkeypatch):
    """WebSocketDisconnected hits the loop's unified handler on attempt 1.

    Mirrors the existing ConnectionClosed flow tests, but for our own
    exception type — the retry budget declared on the action must remain
    untouched and the run must fail with the sanitized CDP reason.
    """
    flow = _flow(
        [
            FlowStepSpec(
                name="Click submit",
                actions=[FlowAction(action="click", selector="#x", retries=3)],
            )
        ]
    )
    loop, _, _, _ = _make_loop(tmp_path)

    async def boom(*args, **kwargs):
        raise WebSocketDisconnected("socket died mid-click")

    dispatch = AsyncMock(side_effect=boom)
    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)

    result = await loop.run_flow(flow)

    assert result["status"] == "failed"
    assert "CDP connection lost" in result["failure_reason"]

    action_report = result["steps"][0]["actions"][0]
    assert action_report["passed"] is False
    assert action_report["attempts"] == 1  # retries NOT consumed
    assert "CDP connection lost" in action_report["failure_reason"]

    # Exactly one dispatch: no retry after a disconnect.
    assert dispatch.await_count == 1
    assert result["summary"]["retries_used"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_kwargs", "breaking_call"),
    [
        ({"action": "click", "selector": "#x"}, "click"),
        ({"action": "type", "selector": "#email", "text": "hunter2"}, "type_text"),
    ],
    ids=["click", "type"],
)
async def test_run_flow_real_executor_disconnect_reaches_unified_handler(
    tmp_path, monkeypatch, action_kwargs, breaking_call
):
    """End-to-end: REAL _dispatch_action + loop unified recovery path.

    Unlike the flow tests above (and the existing test_agent_flow ones),
    nothing patches _dispatch_action here: only the InputDomain CDP call
    dies, so the full chain is exercised — executor re-raise → loop
    except-clause → truthful failed report without retries.
    """
    flow = _flow(
        [
            FlowStepSpec(
                name="Dying step",
                actions=[FlowAction(retries=3, **action_kwargs)],
            )
        ]
    )
    loop, _, _, session = _make_loop(tmp_path)

    failing_input = AsyncMock()
    getattr(failing_input, breaking_call).side_effect = WebSocketDisconnected(
        "socket died mid-action"
    )
    session._input = failing_input

    result = await loop.run_flow(flow)

    assert result["status"] == "failed"
    assert "CDP connection lost" in result["failure_reason"]

    action_report = result["steps"][0]["actions"][0]
    assert action_report["passed"] is False
    assert action_report["attempts"] == 1
    assert "CDP connection lost" in action_report["failure_reason"]
    # The broken CDP call ran exactly once — no retry was attempted.
    assert getattr(failing_input, breaking_call).await_count == 1
