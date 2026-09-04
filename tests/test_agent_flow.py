"""Unit tests for the docs-independent run-flow mode (READY-AI-T-4).

Fixtures required by the card:
- (a) success flow: actions are dispatched, asserts pass, data is extracted
- (b) failed assert: structured result captures expected vs actual
- (c) exhausted retries: attempts are counted per action/step

Also verifies the mode never instantiates DocRenderer and requires no
screenshots or visual annotation.
"""

from __future__ import annotations

import json
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
    """DoD5a — success flow: actions dispatched, asserts pass, data extracted.

    Uses a ``navigate`` action: with the B3 fail-closed classifier only
    ``KNOWN_SILENT_SUCCESS_ACTIONS`` (scroll_to, type, press_key, navigate)
    are assumed passed on their executor wording — anything else must
    carry an explicit success signal, so plain "Clicked element: ..."
    would now be reported as an unrecognized outcome.
    """
    flow = _flow(
        [
            FlowStepSpec(
                name="Go to checkout",
                actions=[FlowAction(action="navigate", url="https://app.example.com/checkout")],
                asserts=[
                    FlowAssertion(type="url_contains", expected="/done"),
                    FlowAssertion(type="element_visible", selector="#receipt"),
                ],
                extract=[FlowExtraction(name="title", selector="h1", attribute="textContent")],
            )
        ]
    )
    loop, page, runtime, input_domain = _make_loop(tmp_path)

    dispatch = AsyncMock(return_value="Navigated to: https://app.example.com/checkout")
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
    assert step["actions"][0]["action"] == "navigate"
    assert step["actions"][0]["attempts"] == 1
    assert step["actions"][0]["passed"] is True
    assert step["actions"][0]["params"] == {"url": "https://app.example.com/checkout"}

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

    # DoD2 — nothing was skipped on a clean success.
    assert step["skipped_asserts"] == 0
    assert step["skipped_extractions"] == 0
    assert result["summary"]["skipped_asserts_total"] == 0
    assert result["summary"]["skipped_extractions_total"] == 0

    # The docs pipeline relies on the same action dispatcher — it must have run.
    assert dispatch.await_count == 1


@pytest.mark.asyncio
async def test_run_flow_reports_failed_assert_with_expected_and_actual(tmp_path, monkeypatch):
    """DoD5b — failed assert: result captures expected vs actual per step."""
    flow = _flow(
        [
            FlowStepSpec(
                name="Confirm order",
                actions=[FlowAction(action="navigate", url="https://app.example.com/confirm")],
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
        AsyncMock(return_value="Navigated to: https://app.example.com/confirm"),
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

    # DoD2 — the declared-but-not-executed expectations/extractions are
    # counted instead of being silently dropped.
    assert step["skipped_asserts"] == 1
    assert step["skipped_extractions"] == 1
    assert result["summary"]["skipped_asserts_total"] == 1
    assert result["summary"]["skipped_extractions_total"] == 1

    assert result["summary"]["actions_failed"] == 1
    assert result["summary"]["retries_used"] == 2


@pytest.mark.asyncio
async def test_run_flow_never_instantiates_doc_renderer(tmp_path, monkeypatch):
    """DoD3 — run-flow mode must not touch DocRenderer or annotation."""
    flow = _flow(
        [
            FlowStepSpec(
                name="Check page",
                actions=[FlowAction(action="navigate", url="https://app.example.com/start")],
                asserts=[FlowAssertion(type="element_present", selector="#app")],
            )
        ]
    )
    loop, page, runtime, input_domain = _make_loop(tmp_path)

    monkeypatch.setattr(
        "src.agent.loop.executor._dispatch_action",
        AsyncMock(return_value="Navigated to: https://app.example.com/start"),
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


# ─── B1 — secret masking at the report boundary ─────────────────────────

@pytest.mark.asyncio
async def test_run_flow_masks_type_action_text_in_params_and_description(tmp_path, monkeypatch):
    """B1 — type text is masked even when the field name is not sensitive.

    The executor echoes the raw value into its description whenever the
    ``is_sensitive_field`` heuristic misses (e.g. field name "code"); the
    report boundary must redact both ``params.text`` and the description.
    """
    flow = _flow(
        [
            FlowStepSpec(
                name="Enter code",
                actions=[FlowAction(action="type", selector="#code", text="Sup3rSecret!")],
                asserts=[FlowAssertion(type="element_present", selector="#submit")],
            )
        ]
    )
    loop, page, runtime, input_domain = _make_loop(tmp_path)

    async def fake_dispatch(payload, *args, **kwargs):
        # Mimic the executor on a non-sensitive-looking field: raw text echo.
        return f"Typed '{payload['text']}' into {payload['selector']}"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", fake_dispatch)
    runtime.query_selector.return_value = "object-id-1"

    result = await loop.run_flow(flow)

    assert result["status"] == "passed"
    step = result["steps"][0]
    action_report = step["actions"][0]
    assert action_report["params"]["text"] == "***"
    assert "***" in action_report["description"]
    assert "Sup3rSecret!" not in action_report["description"]
    # The raw secret must never reach the serialized action reports.
    assert "Sup3rSecret!" not in json.dumps(step["actions"])


@pytest.mark.asyncio
async def test_run_flow_masks_click_text_action_text_in_params_and_description(tmp_path, monkeypatch):
    """B1 — click_text text is masked in params and the description."""
    flow = _flow(
        [
            FlowStepSpec(
                name="Click option",
                actions=[FlowAction(action="click_text", text="TopSecretXYZ")],
            )
        ]
    )
    loop, page, runtime, input_domain = _make_loop(tmp_path)

    async def fake_dispatch(payload, *args, **kwargs):
        return f"Clicked element by text: '{payload['text']}'"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", fake_dispatch)

    result = await loop.run_flow(flow)

    step = result["steps"][0]
    action_report = step["actions"][0]
    assert action_report["params"]["text"] == "***"
    assert "***" in action_report["description"]
    assert "TopSecretXYZ" not in action_report["description"]
    assert "TopSecretXYZ" not in json.dumps(step["actions"])


# ─── B3 — explicit action-outcome classification ─────────────────────────

@pytest.mark.parametrize(
    "description",
    [
        "[Failed] Element not found: #missing",
        "[Error] click: boom",
        "[Unknown action: frobnicate]",
        "Timeout waiting for: #spinner",
    ],
)
def test_flow_action_ok_fails_on_denial_prefixes(description):
    assert AgenticLoop._flow_action_ok(description, "click") is False


@pytest.mark.parametrize(
    "description,action_type",
    [
        ("Scrolled to element: #nav", "scroll_to"),
        ("Typed '***' into #code", "type"),
        ("Pressed key: Enter", "press_key"),
        ("Navigated to: https://app.example.com/a", "navigate"),
    ],
)
def test_flow_silent_success_actions_classified_passed(description, action_type):
    assert AgenticLoop._flow_action_ok(description, action_type) is True


def test_flow_unrecognized_action_outcome_fails_closed():
    assert AgenticLoop._flow_action_ok("Some random wording", "click") is False
    assert (
        AgenticLoop._flow_failure_reason("Some random wording", "click")
        == "unrecognized action outcome"
    )


def test_wait_timeout_classified_as_failed():
    """B3 regression — a `wait` timeout must never look passed."""
    assert AgenticLoop._flow_action_ok("Timeout waiting for: #spinner", "wait") is False
    assert (
        AgenticLoop._flow_failure_reason("Timeout waiting for: #spinner", "wait")
        == "Timeout waiting for: #spinner"
    )


@pytest.mark.parametrize(
    "description,action_type",
    [
        ("Clicked element: #nav-products", "click"),
        ("Clicked element via JS fallback: #nav-products", "click"),
        ("Clicked element by text: 'Products'", "click_text"),
        ("Scrolled down", "scroll"),
        ("Found: #spa-status", "wait"),
        ("Observing current page state", "observe"),
    ],
)
def test_flow_explicit_success_wordings_classified_passed(description, action_type):
    """Slice-1 harness — executor success wording must pass dispatch.

    The Fase-1 E2E proved `click` could never pass run_flow: the classifier
    only trusted the silent set, so every click ended as "unrecognized
    action outcome". Explicit success wordings are allowlisted; the step's
    asserts remain the real verifiers.
    """
    assert AgenticLoop._flow_action_ok(description, action_type) is True


# ─── B4 — truthful run-level CDP disconnect abort ───────────────────────

@pytest.mark.asyncio
async def test_run_flow_aborts_on_cdp_disconnect_with_run_level_reason(tmp_path, monkeypatch):
    """B4 — a CDP disconnect sets the run-level reason and skips the rest."""
    import websockets

    flow = _flow(
        [
            FlowStepSpec(
                name="Step one",
                actions=[FlowAction(action="click", selector="#x")],
            ),
            FlowStepSpec(
                name="Step two",
                actions=[FlowAction(action="click", selector="#y")],
                asserts=[FlowAssertion(type="element_present", selector="#z")],
                extract=[FlowExtraction(name="heading", selector="h1")],
            ),
        ]
    )
    loop, page, runtime, input_domain = _make_loop(tmp_path)

    async def boom(*args, **kwargs):
        raise websockets.exceptions.ConnectionClosed(None, None)

    dispatch = AsyncMock(side_effect=boom)
    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)

    result = await loop.run_flow(flow)

    assert result["status"] == "failed"
    assert result["failure_reason"]
    assert "CDP connection lost" in result["failure_reason"]

    step1 = result["steps"][0]
    assert step1["status"] == "failed"
    assert "CDP connection lost" in step1["actions"][0]["failure_reason"]

    # Remaining steps are truthfully reported as skipped, never re-run.
    step2 = result["steps"][1]
    assert step2["status"] == "skipped"
    assert step2["failure_reason"] == "aborted: CDP connection lost"
    assert step2["actions"] == []
    assert step2["asserts"] == []
    assert step2["extracted"] == []
    assert step2["skipped_asserts"] == 1
    assert step2["skipped_extractions"] == 1

    # Summary counts reflect only executed steps + the explicitly skipped one.
    assert result["summary"]["steps_total"] == 2
    assert result["summary"]["steps_passed"] == 0
    assert result["summary"]["steps_failed"] == 1
    assert result["summary"]["steps_skipped"] == 1

    # No further actions are dispatched and no extra navigation happens.
    assert dispatch.await_count == 1
    assert page.navigate.await_count == 1


# ─── B5 — fail-closed validation ────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_flow_extraction_multiple_zero_matches_returns_empty_list(tmp_path, monkeypatch):
    """B5 — multiple extraction with no matches yields [] instead of null."""
    flow = _flow(
        [
            FlowStepSpec(
                name="No matches",
                actions=[FlowAction(action="navigate", url="https://app.example.com/a")],
                extract=[FlowExtraction(name="empty", selector="#ghost", multiple=True)],
            )
        ]
    )
    loop, page, runtime, input_domain = _make_loop(tmp_path)

    monkeypatch.setattr(
        "src.agent.loop.executor._dispatch_action",
        AsyncMock(return_value="Navigated to: https://app.example.com/a"),
    )
    runtime.evaluate.return_value = []  # zero matches

    result = await loop.run_flow(flow)

    assert result["status"] == "passed"
    assert result["steps"][0]["extracted"][0]["value"] == []


@pytest.mark.asyncio
async def test_run_flow_url_contains_empty_expected_fails_closed(tmp_path, monkeypatch):
    """B5 — url_contains with a missing/empty expected never passes."""
    flow = _flow(
        [
            FlowStepSpec(
                name="Empty expected",
                actions=[FlowAction(action="navigate", url="https://app.example.com/a")],
                asserts=[FlowAssertion(type="url_contains", expected="")],
            )
        ]
    )
    loop, page, runtime, input_domain = _make_loop(tmp_path)

    monkeypatch.setattr(
        "src.agent.loop.executor._dispatch_action",
        AsyncMock(return_value="Navigated to: https://app.example.com/a"),
    )
    runtime.evaluate.return_value = "https://app.example.com/a"

    result = await loop.run_flow(flow)

    assert result["status"] == "failed"
    assertion = result["steps"][0]["asserts"][0]
    assert assertion["passed"] is False
    assert "empty expected value" in assertion["message"]


@pytest.mark.asyncio
async def test_run_flow_text_equals_empty_expected_missing_actual_fails_closed(tmp_path, monkeypatch):
    """B5 — text_equals with empty expected + missing text fails instead of passing."""
    flow = _flow(
        [
            FlowStepSpec(
                name="Compare",
                actions=[FlowAction(action="navigate", url="https://app.example.com/a")],
                asserts=[FlowAssertion(type="text_equals", selector="#missing", expected="")],
            )
        ]
    )
    loop, page, runtime, input_domain = _make_loop(tmp_path)

    monkeypatch.setattr(
        "src.agent.loop.executor._dispatch_action",
        AsyncMock(return_value="Navigated to: https://app.example.com/a"),
    )
    runtime.get_element_text.return_value = ""  # element missing / empty text

    result = await loop.run_flow(flow)

    assert result["status"] == "failed"
    assertion = result["steps"][0]["asserts"][0]
    assert assertion["passed"] is False
    assert "empty expected value" in assertion["message"]


@pytest.mark.asyncio
async def test_run_flow_attribute_equals_empty_expected_missing_actual_fails_closed(tmp_path, monkeypatch):
    """B5 — attribute_equals with empty expected + missing attribute fails."""
    flow = _flow(
        [
            FlowStepSpec(
                name="Compare attr",
                actions=[FlowAction(action="navigate", url="https://app.example.com/a")],
                asserts=[
                    FlowAssertion(
                        type="attribute_equals",
                        selector="#missing",
                        attribute="href",
                        expected="",
                    )
                ],
            )
        ]
    )
    loop, page, runtime, input_domain = _make_loop(tmp_path)

    monkeypatch.setattr(
        "src.agent.loop.executor._dispatch_action",
        AsyncMock(return_value="Navigated to: https://app.example.com/a"),
    )
    runtime.get_element_attributes.return_value = {}

    result = await loop.run_flow(flow)

    assert result["status"] == "failed"
    assertion = result["steps"][0]["asserts"][0]
    assert assertion["passed"] is False
    assert "empty expected value" in assertion["message"]
