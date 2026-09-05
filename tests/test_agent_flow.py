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
from pydantic import ValidationError

from src.agent import executor as executor_module
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

    async def fake_evaluate(expression, session_id=None):
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

    async def fake_evaluate(expression, session_id=None):
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


# ─── PH2A — effect policy taxonomy ─────────────────────────────────────

@pytest.mark.parametrize(
    "action_type,expected",
    [
        ("observe", "read"),
        ("wait", "read"),
        ("navigate", "navigate"),
        ("scroll", "navigate"),
        ("scroll_to", "navigate"),
        ("click", "write"),
        ("click_text", "write"),
        ("type", "write"),
        ("press_key", "write"),
        ("future_action_xyz", "write"),
        ("", None),
        ("[Unknown action: frobnicate]", None),
    ],
)
def test_action_effect_level_taxonomy(action_type, expected):
    assert executor_module.action_effect_level(action_type) == expected


@pytest.mark.parametrize(
    "action_type,policy,expected",
    [
        ("click", "write", True),
        ("click", "navigate", False),
        ("click", "read", False),
        ("navigate", "navigate", True),
        ("navigate", "read", False),
        ("observe", "read", True),
        ("wait", "write", True),
        ("click", "bogus", False),
        ("[Unknown action: x]", "write", False),
    ],
)
def test_action_allowed_under_policy(action_type, policy, expected):
    assert executor_module.action_allowed_under_policy(action_type, policy) is expected


def test_irreversible_requires_confirm_engine_model():
    with pytest.raises(ValidationError):
        FlowStepSpec(actions=[], irreversible=True)
    with pytest.raises(ValidationError):
        FlowStepSpec(actions=[], irreversible=True, confirm=False)
    ok = FlowStepSpec(actions=[], irreversible=True, confirm=True)
    assert ok.confirm is True


def test_irreversible_requires_confirm_sdk_model():
    from ready_ai.models import FlowStep as _PublicFlowStep

    with pytest.raises(ValidationError):
        _PublicFlowStep(actions=[], irreversible=True)
    ok = _PublicFlowStep(actions=[], irreversible=True, confirm=True)
    assert ok.confirm is True


def test_sdk_effect_policy_plumbs_to_engine_ceiling():
    from ready_ai import Flow as _PublicFlow
    from ready_ai import FlowStep as _PublicFlowStep
    from ready_ai.client import _to_flow_spec
    from ready_ai.models import EffectPolicy as _Policy

    spec = _to_flow_spec(
        _PublicFlow(
            url="https://app.example.com",
            steps=[_PublicFlowStep()],
            effect_policy=_Policy.OBSERVE,
        ),
        run_id="x",
        headless=True,
        model="m",
    )
    assert spec.effect_policy == "read"


# ─── PH2A — gates (mocked session, no browser) ─────────────────────────

async def test_step_policy_ceiling_denies_without_dispatch(tmp_path, monkeypatch):
    flow = _flow(
        [
            FlowStepSpec(
                name="No clicks under read",
                policy="read",
                actions=[FlowAction(action="click", selector="#x")],
            )
        ]
    )
    loop, _page, _runtime, _input = _make_loop(tmp_path)
    dispatch = AsyncMock(side_effect=AssertionError("must not execute"))
    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)

    result = await loop.run_flow(flow)

    assert result["status"] == "failed"
    step = result["steps"][0]
    assert step["status"] == "failed"
    assert "ceiling 'read'" in step["failure_reason"]
    assert "nothing was executed" in step["failure_reason"]
    dispatch.assert_not_called()


async def test_flow_effect_policy_enforced(tmp_path, monkeypatch):
    flow = _flow(
        [FlowStepSpec(name="Click", actions=[FlowAction(action="click", selector="#x")])],
    )
    flow.effect_policy = "navigate"
    loop, _page, _runtime, _input = _make_loop(tmp_path)
    dispatch = AsyncMock(side_effect=AssertionError("must not execute"))
    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)

    result = await loop.run_flow(flow)

    assert result["status"] == "failed"
    assert "ceiling 'navigate'" in result["steps"][0]["failure_reason"]
    dispatch.assert_not_called()


async def test_confirm_step_reports_pending_without_dispatch(tmp_path, monkeypatch):
    flow = _flow(
        [
            FlowStepSpec(
                name="Danger",
                confirm=True,
                actions=[FlowAction(action="click", selector="#x")],
            )
        ]
    )
    loop, _page, _runtime, _input = _make_loop(tmp_path)
    dispatch = AsyncMock(side_effect=AssertionError("must not execute"))
    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)

    result = await loop.run_flow(flow)

    assert result["status"] == "pending_confirmation"
    step = result["steps"][0]
    assert step["status"] == "pending_confirmation"
    assert step["idempotency_key"] == "flow-test:step-1"
    assert "nothing was executed" in step["failure_reason"]
    dispatch.assert_not_called()


async def test_confirm_resume_executes_and_records_key(tmp_path, monkeypatch):
    flow = _flow(
        [
            FlowStepSpec(
                name="Danger",
                confirm=True,
                actions=[FlowAction(action="click", selector="#x")],
            )
        ]
    )
    loop, _page, _runtime, _input = _make_loop(tmp_path)
    dispatch = AsyncMock(return_value="Clicked element: #x")
    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)

    result = await loop.run_flow(flow, confirm={"flow-test:step-1"})

    assert result["status"] == "passed"
    assert result["steps"][0]["status"] == "passed"
    assert "flow-test:step-1" in loop._state.confirmed_effects
    dispatch.assert_called_once()


async def test_idempotent_replay_skips_execution(tmp_path, monkeypatch):
    flow = _flow(
        [
            FlowStepSpec(
                name="Danger",
                confirm=True,
                actions=[FlowAction(action="click", selector="#x")],
            )
        ]
    )
    loop, _page, _runtime, _input = _make_loop(tmp_path)
    loop._state.confirmed_effects.append("flow-test:step-1")
    dispatch = AsyncMock(side_effect=AssertionError("must not re-execute"))
    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)

    result = await loop.run_flow(flow, confirm={"flow-test:step-1"})

    assert result["status"] == "passed"
    step = result["steps"][0]
    assert step["status"] == "passed"
    assert step["confirmation"] == "idempotent-replay"
    assert step["actions"] == []
    dispatch.assert_not_called()


# ─── PH2B — tab actions (mocked page, no browser) ──────────────────────

@pytest.mark.parametrize(
    "action_type,expected",
    [
        ("wait_for_popup", "read"),
        ("switch_tab", "navigate"),
        ("close_tab", "navigate"),
    ],
)
def test_tab_actions_taxonomy(action_type, expected):
    assert executor_module.action_effect_level(action_type) == expected


@pytest.mark.parametrize(
    "description,action_type",
    [
        ("Popup opened: abc123...", "wait_for_popup"),
        ("Switched to tab: https://x/popup", "switch_tab"),
        ("Closed tab: abc123...", "close_tab"),
    ],
)
def test_tab_action_wordings_classified_passed(description, action_type):
    assert AgenticLoop._flow_action_ok(description, action_type) is True


async def test_wait_for_popup_success(tmp_path, monkeypatch):
    flow = _flow(
        [FlowStepSpec(actions=[FlowAction(action="wait_for_popup")])]
    )
    loop, page, _runtime, _input = _make_loop(tmp_path)
    page.wait_for_popup = AsyncMock(
        return_value={"target_id": "t-popup", "session_id": "s-popup"}
    )
    result = await loop.run_flow(flow)
    assert result["status"] == "passed"
    assert result["steps"][0]["actions"][0]["description"].startswith("Popup opened")


async def test_wait_for_popup_timeout_fails(tmp_path, monkeypatch):
    flow = _flow(
        [FlowStepSpec(actions=[FlowAction(action="wait_for_popup")])]
    )
    loop, page, _runtime, _input = _make_loop(tmp_path)
    page.wait_for_popup = AsyncMock(side_effect=TimeoutError("timed out"))
    result = await loop.run_flow(flow)
    assert result["status"] == "failed"
    assert "No popup opened" in result["steps"][0]["failure_reason"]


async def test_switch_tab_requires_target(tmp_path, monkeypatch):
    flow = _flow([FlowStepSpec(actions=[FlowAction(action="switch_tab")])])
    loop, _page, _runtime, _input = _make_loop(tmp_path)
    result = await loop.run_flow(flow)
    assert result["status"] == "failed"
    assert "requires 'target'" in result["steps"][0]["failure_reason"]


async def test_switch_tab_unknown_names_context(tmp_path, monkeypatch):
    flow = _flow(
        [FlowStepSpec(actions=[FlowAction(action="switch_tab", target="ghost")])]
    )
    loop, page, _runtime, _input = _make_loop(tmp_path)
    page.switch_to_tab = AsyncMock(side_effect=RuntimeError("unknown tab 'ghost' (targets: none)"))
    result = await loop.run_flow(flow)
    assert result["status"] == "failed"
    assert "unknown tab 'ghost'" in result["steps"][0]["failure_reason"]


async def test_switch_tab_success(tmp_path, monkeypatch):
    flow = _flow(
        [FlowStepSpec(actions=[FlowAction(action="switch_tab", target="/popup")])]
    )
    loop, page, _runtime, _input = _make_loop(tmp_path)
    page.switch_to_tab = AsyncMock(
        return_value={"target_id": "t-popup", "url": "https://x/popup"}
    )
    result = await loop.run_flow(flow)
    assert result["status"] == "passed"
    page.switch_to_tab.assert_awaited_once_with("/popup")


async def test_close_tab_success(tmp_path, monkeypatch):
    flow = _flow(
        [FlowStepSpec(actions=[FlowAction(action="close_tab", target="/popup")])]
    )
    loop, page, _runtime, _input = _make_loop(tmp_path)
    page.close_tab = AsyncMock(return_value={"closed": "t-popup", "active": "t-main"})
    result = await loop.run_flow(flow)
    assert result["status"] == "passed"


async def test_click_with_target_routes_session(tmp_path, monkeypatch):
    flow = _flow(
        [
            FlowStepSpec(
                actions=[FlowAction(action="click", selector="#x", target="/popup")]
            )
        ]
    )
    loop, page, _runtime, input_domain = _make_loop(tmp_path)
    page.resolve_target_session = AsyncMock(return_value="sess-popup")
    input_domain.click = AsyncMock(return_value=True)
    result = await loop.run_flow(flow)
    assert result["status"] == "passed"
    input_domain.click.assert_awaited_once_with("#x", session_id="sess-popup")


async def test_click_with_unknown_target_fails_closed(tmp_path, monkeypatch):
    flow = _flow(
        [
            FlowStepSpec(
                actions=[FlowAction(action="click", selector="#x", target="ghost")]
            )
        ]
    )
    loop, page, _runtime, input_domain = _make_loop(tmp_path)
    page.resolve_target_session = AsyncMock(
        side_effect=RuntimeError("unknown tab 'ghost' (targets: none)")
    )
    input_domain.click = AsyncMock(return_value=True)
    result = await loop.run_flow(flow)
    assert result["status"] == "failed"
    assert "unknown tab 'ghost'" in result["steps"][0]["failure_reason"]
    input_domain.click.assert_not_called()


async def test_type_with_target_fails_closed(tmp_path, monkeypatch):
    flow = _flow(
        [
            FlowStepSpec(
                actions=[
                    FlowAction(action="type", selector="#x", text="hi", target="/popup")
                ]
            )
        ]
    )
    loop, _page, _runtime, _input = _make_loop(tmp_path)
    result = await loop.run_flow(flow)
    assert result["status"] == "failed"
    assert "does not support explicit target" in result["steps"][0]["failure_reason"]


# ─── PH2C — files and dialogs (mocked session, no browser) ────────────

@pytest.mark.parametrize(
    "action_type,expected",
    [
        ("upload", "write"),
        ("download", "write"),
        ("dialog", "write"),
    ],
)
def test_files_dialogs_taxonomy(action_type, expected):
    assert executor_module.action_effect_level(action_type) == expected


@pytest.mark.parametrize(
    "description,action_type",
    [
        ("Uploaded 2 file(s) to #f", "upload"),
        ("Downloaded r.csv (10B)", "download"),
        ("Dialog accepted (confirm): Clicked element: #x", "dialog"),
        ("Dialog dismissed (alert): Clicked element: #x", "dialog"),
    ],
)
def test_files_dialogs_wordings_classified_passed(description, action_type):
    assert AgenticLoop._flow_action_ok(description, action_type) is True


async def test_upload_rejects_outside_allowlist(tmp_path, monkeypatch):
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    flow = _flow(
        [
            FlowStepSpec(
                actions=[
                    FlowAction(
                        action="upload",
                        selector="#f",
                        paths=[str(outside)],
                        roots=[str(tmp_path / "allowed")],
                    )
                ]
            )
        ]
    )
    loop, page, _runtime, input_domain = _make_loop(tmp_path)
    input_domain.set_files = AsyncMock(return_value=True)
    result = await loop.run_flow(flow)
    assert result["status"] == "failed"
    assert "outside allowlist" in result["steps"][0]["failure_reason"]
    input_domain.set_files.assert_not_called()


async def test_upload_requires_roots(tmp_path, monkeypatch):
    flow = _flow(
        [FlowStepSpec(actions=[FlowAction(action="upload", selector="#f", paths=["/a"])])]
    )
    loop, _page, _runtime, _input = _make_loop(tmp_path)
    result = await loop.run_flow(flow)
    assert result["status"] == "failed"
    assert "allowlist" in result["steps"][0]["failure_reason"]


async def test_upload_missing_file(tmp_path, monkeypatch):
    flow = _flow(
        [
            FlowStepSpec(
                actions=[
                    FlowAction(
                        action="upload",
                        selector="#f",
                        paths=[str(tmp_path / "nope.txt")],
                        roots=[str(tmp_path)],
                    )
                ]
            )
        ]
    )
    loop, _page, _runtime, _input = _make_loop(tmp_path)
    result = await loop.run_flow(flow)
    assert result["status"] == "failed"
    assert "not found" in result["steps"][0]["failure_reason"]


async def test_upload_success_masks_path(tmp_path, monkeypatch):
    target = tmp_path / "doc.txt"
    target.write_text("data")
    flow = _flow(
        [
            FlowStepSpec(
                actions=[
                    FlowAction(
                        action="upload",
                        selector="#f",
                        paths=[str(target)],
                        roots=[str(tmp_path)],
                    )
                ]
            )
        ]
    )
    loop, _page, _runtime, input_domain = _make_loop(tmp_path)
    input_domain.set_files = AsyncMock(return_value=True)
    result = await loop.run_flow(flow)
    assert result["status"] == "passed"
    desc = result["steps"][0]["actions"][0]["description"]
    assert desc == "Uploaded 1 file(s) to #f"
    assert str(target) not in desc


async def test_download_no_start_fails(tmp_path, monkeypatch):
    flow = _flow(
        [FlowStepSpec(actions=[FlowAction(action="download", selector="#dl")])]
    )
    loop, page, _runtime, input_domain = _make_loop(tmp_path)
    input_domain.click = AsyncMock(return_value=True)
    page.wait_for_download = AsyncMock(return_value=None)
    result = await loop.run_flow(flow)
    assert result["status"] == "failed"
    assert "did not start" in result["steps"][0]["failure_reason"]


async def test_download_verifies_file(tmp_path, monkeypatch):
    landed = tmp_path / "r.csv"
    landed.write_text("a,b\n1,2\n")
    evidence = MagicMock()
    evidence.details = {"filename": "r.csv"}
    flow = _flow(
        [
            FlowStepSpec(
                actions=[
                    FlowAction(action="download", selector="#dl", filename="r.csv")
                ]
            )
        ]
    )
    loop, page, _runtime, input_domain = _make_loop(tmp_path)
    input_domain.click = AsyncMock(return_value=True)
    page.wait_for_download = AsyncMock(return_value=evidence)
    page.download_dir = str(tmp_path)
    result = await loop.run_flow(flow)
    assert result["status"] == "passed"
    assert "Downloaded r.csv (" in result["steps"][0]["actions"][0]["description"]


async def test_dialog_rejects_bad_decision(tmp_path, monkeypatch):
    flow = _flow(
        [
            FlowStepSpec(
                actions=[
                    FlowAction(
                        action="dialog",
                        decision="maybe",
                        then={"action": "click", "selector": "#x"},
                    )
                ]
            )
        ]
    )
    loop, page, _runtime, _input = _make_loop(tmp_path)
    page.subscribe_dialogs = MagicMock()
    result = await loop.run_flow(flow)
    assert result["status"] == "failed"
    assert "must be 'accept' or 'dismiss'" in result["steps"][0]["failure_reason"]
    page.subscribe_dialogs.assert_not_called()


async def test_dialog_accept_flow(tmp_path, monkeypatch):
    flow = _flow(
        [
            FlowStepSpec(
                actions=[
                    FlowAction(
                        action="dialog",
                        decision="accept",
                        then={"action": "click", "selector": "#x"},
                    )
                ]
            )
        ]
    )
    loop, page, _runtime, input_domain = _make_loop(tmp_path)
    sub = MagicMock()
    sub.wait = AsyncMock(return_value={"params": {"type": "confirm"}})
    sub.close = MagicMock()
    page.subscribe_dialogs = MagicMock(return_value=sub)
    page.handle_dialog = AsyncMock()
    input_domain.click = AsyncMock(return_value=True)
    result = await loop.run_flow(flow)
    assert result["status"] == "passed"
    desc = result["steps"][0]["actions"][0]["description"]
    assert desc.startswith("Dialog accepted (confirm)")
    page.handle_dialog.assert_awaited_once_with(True, None)


async def test_dialog_no_dialog_opened_fails(tmp_path, monkeypatch):
    flow = _flow(
        [
            FlowStepSpec(
                actions=[
                    FlowAction(
                        action="dialog",
                        decision="dismiss",
                        then={"action": "click", "selector": "#x"},
                    )
                ]
            )
        ]
    )
    loop, page, _runtime, input_domain = _make_loop(tmp_path)
    sub = MagicMock()
    sub.wait = AsyncMock(side_effect=TimeoutError("timed out"))
    sub.close = MagicMock()
    page.subscribe_dialogs = MagicMock(return_value=sub)
    input_domain.click = AsyncMock(return_value=True)
    result = await loop.run_flow(flow)
    assert result["status"] == "failed"
    assert "did not open" in result["steps"][0]["failure_reason"]


# ─── PH2D - human checkpoint pause/resume (mocked session, no browser) ───

_HUMAN_STEP = {
    "action": "await_human",
    "reason": "Complete the SSO login in your own browser",
    "resume_when": {"selector_visible": "#authed"},
}


def _human_flow():
    return _flow(
        [
            FlowStepSpec(
                name="Land",
                actions=[FlowAction(action="observe")],
            ),
            FlowStepSpec(
                name="Human SSO",
                actions=[FlowAction(**_HUMAN_STEP)],
            ),
            FlowStepSpec(
                name="Verify",
                actions=[FlowAction(action="observe")],
                asserts=[
                    FlowAssertion(type="element_present", selector="#authed")
                ],
            ),
        ]
    )


async def test_await_human_pauses_with_observable_condition(tmp_path, monkeypatch):
    async def dispatch(payload, *args, **kwargs):
        if payload.get("action") == "observe":
            return "Observing current page state"
        raise AssertionError(f"must not dispatch: {payload!r}")

    monkeypatch.setattr(
        "src.agent.loop.executor._dispatch_action", dispatch
    )
    loop, _page, _runtime, _input = _make_loop(tmp_path, run_id="sso-1")
    result = await loop.run_flow(_human_flow())
    assert result["status"] == "paused"
    assert result["summary"]["steps_paused"] == 1
    assert result["steps"][0]["status"] == "passed"
    paused = result["steps"][1]
    assert paused["status"] == "paused"
    assert paused["pause"]["reason"] == _HUMAN_STEP["reason"]
    assert paused["pause"]["resume_when"] == _HUMAN_STEP["resume_when"]
    assert paused["pause"]["step_index"] == 2
    assert paused["pause"]["run_id"] == "sso-1"
    assert result["pause"] == paused["pause"]
    assert result["steps"][2]["status"] == "skipped"
    assert "paused at step 2" in result["steps"][2]["failure_reason"]
    assert loop._state.status == "PAUSED"
    assert loop._state.current_step_index == 2
    assert loop._state.pause_reason == _HUMAN_STEP["reason"]
    assert loop._state.resume_when == _HUMAN_STEP["resume_when"]


async def test_await_human_must_be_the_only_action(tmp_path, monkeypatch):
    flow = _flow(
        [
            FlowStepSpec(
                actions=[
                    FlowAction(action="observe"),
                    FlowAction(**_HUMAN_STEP),
                ]
            )
        ]
    )
    loop, _page, _runtime, _input = _make_loop(tmp_path)
    result = await loop.run_flow(flow)
    assert result["status"] == "failed"
    assert "only action" in result["steps"][0]["failure_reason"]


@pytest.mark.parametrize(
    "override",
    [
        {"reason": "  "},
        {"resume_when": {}},
        {"resume_when": {"url_contains": ""}},
        {"resume_when": {"cookie_present": "x"}},
    ],
)
async def test_await_human_malformed_fails_closed(tmp_path, monkeypatch, override):
    params = {**_HUMAN_STEP, **override}
    flow = _flow([FlowStepSpec(actions=[FlowAction(**params)])])
    loop, _page, _runtime, _input = _make_loop(tmp_path)
    result = await loop.run_flow(flow)
    assert result["status"] == "failed"
    assert result["steps"][0]["status"] == "failed"


async def test_resume_continues_at_paused_step_without_reexecution(
    tmp_path, monkeypatch
):
    """Pause, persist the checkpoint, resume by run_id: pre-pause steps are
    never re-executed, the checkpoint step is satisfied (not re-paused)."""
    calls: list[str] = []

    async def dispatch(payload, *args, **kwargs):
        calls.append(payload.get("action"))
        return "Observing current page state"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)
    loop, _page, _runtime, _input = _make_loop(tmp_path, run_id="sso-2")
    paused_result = await loop.run_flow(_human_flow())
    assert paused_result["status"] == "paused"
    checkpoint = tmp_path / "sso-2_state.json"
    loop._state.to_file(checkpoint)
    assert len(calls) == 1  # only the pre-pause observe ran

    # The human acted: #authed is now visible. Resume by run_id.
    _runtime.evaluate = AsyncMock(return_value=True)
    _runtime.query_selector = AsyncMock(return_value={"nodeId": 1})
    loop2, _p2, runtime2, _i2 = _make_loop(tmp_path, run_id="sso-2")
    from src.agent.state import RunState

    loop2._state = RunState.from_file(checkpoint)
    loop2.resume_from = str(checkpoint)
    assert loop2._state is not None and loop2._state.status == "PAUSED"
    runtime2.evaluate = AsyncMock(return_value=True)
    runtime2.query_selector = AsyncMock(return_value={"nodeId": 1})
    result = await loop2.run_flow(_human_flow())
    assert result["status"] == "passed", result
    assert result["summary"]["resumed_from"] == str(checkpoint)
    assert result["steps"][0]["status"] == "skipped"
    assert "not re-executed" in result["steps"][0]["failure_reason"]
    resumed_step = result["steps"][1]
    assert resumed_step["status"] == "passed"
    assert resumed_step.get("confirmation") == "human-checkpoint-resumed"
    assert result["steps"][2]["status"] == "passed"


async def test_resume_blocked_when_condition_unmet(tmp_path, monkeypatch):
    async def dispatch(payload, *args, **kwargs):
        return "Observing current page state"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)
    loop, _page, _runtime, _input = _make_loop(tmp_path, run_id="sso-3")
    paused_result = await loop.run_flow(_human_flow())
    assert paused_result["status"] == "paused"
    checkpoint = tmp_path / "sso-3_state.json"
    loop._state.to_file(checkpoint)

    # Nothing changed in the browser: #authed still absent.
    loop2, _p2, runtime2, _i2 = _make_loop(tmp_path, run_id="sso-3")
    from src.agent.state import RunState

    loop2._state = RunState.from_file(checkpoint)
    runtime2.evaluate = AsyncMock(return_value=False)
    result = await loop2.run_flow(_human_flow())
    assert result["status"] == "failed", result
    assert "resume condition not met" in (result["failure_reason"] or "")
    assert result["steps"][1]["status"] == "skipped"


async def test_resume_rejects_foreign_run_id(tmp_path):
    from src.agent.state import RunState

    state = RunState(run_id="other-run", goal="g", url="https://x.example/")
    state.status = "PAUSED"
    state.current_step_index = 1
    checkpoint = tmp_path / "other-run_state.json"
    state.to_file(checkpoint)
    loop = AgenticLoop(
        goal="run-flow",
        url="https://app.example.com/start",
        output_dir=str(tmp_path),
        run_id="my-run",
        headless=True,
        resume_from=str(checkpoint),
    )
    with pytest.raises(ValueError, match="does not match"):
        await loop.run_flow(_human_flow())


async def test_resume_rejects_incompatible_index(tmp_path):
    from src.agent.state import RunState

    state = RunState(run_id="my-run", goal="g", url="https://x.example/")
    state.status = "PAUSED"
    state.current_step_index = 99
    checkpoint = tmp_path / "my-run_state.json"
    state.to_file(checkpoint)
    loop, _page, _runtime, _input = _make_loop(tmp_path, run_id="my-run")
    loop.resume_from = str(checkpoint)
    loop._state = RunState.from_file(checkpoint)
    with pytest.raises(ValueError, match="outside"):
        await loop.run_flow(_human_flow())


async def test_dialog_prompt_text_masked_in_reports(tmp_path, monkeypatch):
    flow = _flow(
        [
            FlowStepSpec(
                actions=[
                    FlowAction(
                        action="dialog",
                        decision="accept",
                        text="s3cr3t-answer",
                        then={"action": "click", "selector": "#x"},
                    )
                ]
            )
        ]
    )
    loop, page, _runtime, input_domain = _make_loop(tmp_path)
    sub = MagicMock()
    sub.wait = AsyncMock(return_value={"params": {"type": "prompt"}})
    sub.close = MagicMock()
    page.subscribe_dialogs = MagicMock(return_value=sub)
    page.handle_dialog = AsyncMock()
    input_domain.click = AsyncMock(return_value=True)
    result = await loop.run_flow(flow)
    assert result["status"] == "passed"
    action_report = result["steps"][0]["actions"][0]
    assert action_report["params"].get("text") == "***"
    assert "s3cr3t-answer" not in action_report["description"]
    persisted = json.loads((tmp_path / "flow-test_flow_result.json").read_text())
    assert "s3cr3t-answer" not in json.dumps(persisted)


async def test_checkpoint_and_result_carry_no_secrets(tmp_path, monkeypatch):
    """DoD4 (unit half): typed credentials never reach checkpoint/result files."""
    secret = "s3cr3t-pw-9z"

    async def dispatch(payload, *args, **kwargs):
        if payload.get("action") == "type":
            return f"Typed text into: {payload.get('selector')}"
        return "Observing current page state"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)
    flow = _flow(
        [
            FlowStepSpec(
                actions=[FlowAction(action="type", selector="#pw", text=secret)],
            ),
            FlowStepSpec(actions=[FlowAction(**_HUMAN_STEP)]),
        ]
    )
    loop, _page, _runtime, _input = _make_loop(tmp_path, run_id="sec-1")
    loop._save_checkpoint = MagicMock(
        side_effect=lambda status=None: AgenticLoop._save_checkpoint(loop, status)
    )
    result = await loop.run_flow(flow)
    assert result["status"] == "paused"
    checkpoint_text = (tmp_path / "sec-1_state.json").read_text(encoding="utf-8")
    result_text = (tmp_path / "sec-1_flow_result.json").read_text(encoding="utf-8")
    assert secret not in checkpoint_text
    assert secret not in result_text


async def test_profile_dir_reaches_browser_session(tmp_path):
    loop = AgenticLoop(
        goal="run-flow",
        url="https://app.example.com/start",
        output_dir=str(tmp_path),
        run_id="prof-1",
        headless=True,
        profile_dir="/profiles/qa",
    )
    assert loop._session.profile_dir == "/profiles/qa"
    assert loop._session._temp_profile_dir is None
    plain = AgenticLoop(
        goal="run-flow",
        url="https://app.example.com/start",
        output_dir=str(tmp_path),
        run_id="prof-2",
        headless=True,
    )
    assert plain._session.profile_dir is None
