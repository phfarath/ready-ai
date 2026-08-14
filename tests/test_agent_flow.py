"""Unit tests for the docs-independent run-flow mode (READY-AI-T-4).

Fixtures required by the card:
- (a) success flow: actions are dispatched, asserts pass, data is extracted
- (b) failed assert: structured result captures expected vs actual
- (c) exhausted retries: attempts are counted per action/step

Also verifies the mode never instantiates DocRenderer and requires no
screenshots or visual annotation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.loop import AgenticLoop
from src.api.models import (
    FlowSpec,
    FlowStepSpec,
    FlowAction,
    FlowAssertion,
    FlowExtraction,
)


def _flow(steps, *, retries=1):
    return FlowSpec(
        name="checkout",
        url="https://app.example.com/start",
        retries=retries,
        steps=steps,
    )


def _make_loop(tmp_path, run_id="flow-test"):
    """AgenticLoop with a fully mocked BrowserSession."""
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

    input_domain = MagicMock()

    # BrowserSession exposes page/runtime/input_domain as read-only
    # properties; tests inject the stubs via the private attributes.
    session._page = page
    session._runtime = runtime
    session._input = input_domain

    loop._save_checkpoint = MagicMock(return_value=None)
    return loop, page, runtime, input_domain


@pytest.mark.asyncio
async def test_run_flow_success_reports_actions_asserts_and_extractions(tmp_path, monkeypatch):
    """DoD5a — success flow: actions dispatched, asserts pass, data extracted."""
    flow = _flow(
        [
            FlowStepSpec(
                name="Go to checkout",
                actions=[FlowAction(action="click", selector="#checkout-btn")],
                asserts=[
                    FlowAssertion(type="url_contains", expected="/done"),
                    FlowAssertion(type="element_visible", selector="#receipt"),
                ],
                extract=[FlowExtraction(name="title", selector="h1", attribute="textContent")],
            )
        ]
    )
    loop, page, runtime, input_domain = _make_loop(tmp_path)

    dispatch = AsyncMock(return_value="Clicked element: #checkout-btn")
    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)

    async def fake_evaluate(expression):
        if "window.location.href" in expression:
            return "https://app.example.com/done"
        if "#receipt" in expression:
            return True
        if 'h1' in expression:
            return "Checkout summary"
        return None

    runtime.evaluate.side_effect = fake_evaluate

    result = await loop.run_flow(flow)

    assert result["status"] == "passed"
    assert result["flow"] == "checkout"
    assert result["url"] == "https://app.example.com/start"

    step = result["steps"][0]
    assert step["index"] == 1
    assert step["name"] == "Go to checkout"
    assert step["status"] == "passed"
    assert step["attempts"] == 1
    assert step["actions"][0]["action"] == "click"
    assert step["actions"][0]["attempts"] == 1
    assert step["actions"][0]["passed"] is True
    assert step["actions"][0]["params"] == {"selector": "#checkout-btn"}

    assert step["asserts"][0]["type"] == "url_contains"
    assert step["asserts"][0]["expected"] == "/done"
    assert step["asserts"][0]["actual"] == "https://app.example.com/done"
    assert step["asserts"][0]["passed"] is True
    assert step["asserts"][1]["type"] == "element_visible"
    assert step["asserts"][1]["passed"] is True

    assert step["extracted"][0]["name"] == "title"
    assert step["extracted"][0]["value"] == "Checkout summary"

    assert result["summary"]["steps_total"] == 1
    assert result["summary"]["steps_passed"] == 1
    assert result["summary"]["steps_failed"] == 0
    assert result["summary"]["asserts_total"] == 2
    assert result["summary"]["asserts_failed"] == 0
    assert result["summary"]["extractions"] == 1

    # The docs pipeline relies on the same action dispatcher — it must have run.
    assert dispatch.await_count == 1


@pytest.mark.asyncio
async def test_run_flow_reports_failed_assert_with_expected_and_actual(tmp_path, monkeypatch):
    """DoD5b — failed assert: result captures expected vs actual per step."""
    flow = _flow(
        [
            FlowStepSpec(
                name="Confirm order",
                actions=[FlowAction(action="click", selector="#confirm")],
                asserts=[
                    FlowAssertion(
                        type="url_contains",
                        expected="/done",
                        message="Order page was not reached",
                    )
                ],
            )
        ]
    )
    loop, page, runtime, input_domain = _make_loop(tmp_path)

    monkeypatch.setattr(
        "src.agent.loop.executor._dispatch_action",
        AsyncMock(return_value="Clicked element: #confirm"),
    )

    async def fake_evaluate(expression):
        if "window.location.href" in expression:
            return "https://app.example.com/cancelled"
        return None

    runtime.evaluate.side_effect = fake_evaluate

    result = await loop.run_flow(flow)

    assert result["status"] == "failed"
    step = result["steps"][0]
    assert step["status"] == "failed"
    assert step["actions"][0]["passed"] is True

    assertion = step["asserts"][0]
    assert assertion["passed"] is False
    assert assertion["type"] == "url_contains"
    assert assertion["expected"] == "/done"
    assert assertion["actual"] == "https://app.example.com/cancelled"
    assert step["failure_reason"] == "Order page was not reached"

    assert result["summary"]["steps_failed"] == 1
    assert result["summary"]["asserts_failed"] == 1


@pytest.mark.asyncio
async def test_run_flow_reports_exhausted_retries_per_action(tmp_path, monkeypatch):
    """DoD5c — exhausted retries: attempts counted, remaining actions aborted."""
    flow = _flow(
        [
            FlowStepSpec(
                name="Click missing button",
                actions=[
                    FlowAction(action="click", selector="#missing"),
                    FlowAction(action="observe"),
                ],
                asserts=[FlowAssertion(type="element_present", selector="#ghost")],
                extract=[FlowExtraction(name="heading", selector="h1")],
            )
        ],
        retries=2,
    )
    loop, page, runtime, input_domain = _make_loop(tmp_path)

    monkeypatch.setattr(
        "src.agent.loop.executor._dispatch_action",
        AsyncMock(return_value="[Failed] Element not found: #missing"),
    )

    result = await loop.run_flow(flow)

    assert result["status"] == "failed"
    step = result["steps"][0]
    assert step["status"] == "failed"

    action_report = step["actions"][0]
    assert action_report["action"] == "click"
    assert action_report["passed"] is False
    assert action_report["attempts"] == 3  # 1 initial + 2 retries
    assert "[Failed] Element not found" in action_report["failure_reason"]

    # A failed action aborts the rest of the step for an unambiguous report.
    assert len(step["actions"]) == 1
    assert step["asserts"] == []
    assert step["extracted"] == []
    assert step["attempts"] == 3

    assert result["summary"]["actions_failed"] == 1
    assert result["summary"]["retries_used"] == 2


@pytest.mark.asyncio
async def test_run_flow_never_instantiates_doc_renderer(tmp_path, monkeypatch):
    """DoD3 — run-flow mode must not touch DocRenderer or annotation."""
    flow = _flow(
        [
            FlowStepSpec(
                name="Check page",
                actions=[FlowAction(action="observe")],
                asserts=[FlowAssertion(type="element_present", selector="#app")],
            )
        ]
    )
    loop, page, runtime, input_domain = _make_loop(tmp_path)

    monkeypatch.setattr(
        "src.agent.loop.executor._dispatch_action",
        AsyncMock(return_value="Observing current page state"),
    )
    runtime.query_selector.return_value = "object-id-1"  # #app exists

    with patch("src.agent.loop.DocRenderer") as doc_renderer_cls, patch(
        "src.agent.loop.LLMClient"
    ) as llm_cls:
        doc_renderer_cls.side_effect = AssertionError(
            "DocRenderer must never be instantiated in run-flow mode"
        )
        llm_cls.side_effect = AssertionError(
            "LLMClient must not be created in run-flow mode without credentials"
        )
        result = await loop.run_flow(flow)

    assert result["status"] == "passed"
    doc_renderer_cls.assert_not_called()
    llm_cls.assert_not_called()
    # No screenshot is captured in flow mode.
    page.screenshot.assert_not_called()


@pytest.mark.asyncio
async def test_run_flow_reports_cdp_disconnect_as_action_failure(tmp_path, monkeypatch):
    """A dying CDP connection surfaces as a failed action, not a hang."""
    import websockets

    flow = _flow([FlowStepSpec(actions=[FlowAction(action="click", selector="#x")])])
    loop, page, runtime, input_domain = _make_loop(tmp_path)

    async def boom(*args, **kwargs):
        raise websockets.exceptions.ConnectionClosed(None, None)

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", boom)

    result = await loop.run_flow(flow)

    assert result["status"] == "failed"
    step = result["steps"][0]
    assert step["status"] == "failed"
    assert step["actions"][0]["passed"] is False
    assert "CDP" in step["actions"][0]["failure_reason"]
