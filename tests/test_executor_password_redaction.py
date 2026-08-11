import json

import pytest
from unittest.mock import AsyncMock

from src.agent.executor import _dispatch_action


def _make_evaluator(return_value, *, expected_selector=None):
    """Build a side_effect that enforces RuntimeDomain.evaluate's real signature.

    RuntimeDomain.evaluate(expression: str) accepts exactly ONE positional
    argument. A bare AsyncMock accepts any args and masks calling-convention
    bugs, so we use a side_effect that asserts:
      1. evaluate is called with exactly ONE positional argument, and
      2. that argument is an IIFE string (() => { ... })(), and
      3. (when a selector is expected) the selector is embedded via json.dumps.

    The captured expression is stored on ``side_effect.last_expr`` for further
    assertions by the caller.
    """

    def side_effect(expression, *extra):
        # Enforce single-argument calling convention.
        assert extra == (), (
            f"runtime.evaluate accepts ONE positional arg, got extra: {extra!r}"
        )
        # Enforce the IIFE pattern.
        assert expression.startswith("(() =>"), (
            f"JS must be an IIFE (() => {{...}})(), got: {expression[:60]!r}"
        )
        assert expression.rstrip().endswith("})()"), (
            f"JS must be invoked with trailing '()', got tail: {expression[-20:]!r}"
        )
        if expected_selector is not None:
            assert json.dumps(expected_selector) in expression, (
                f"selector must be embedded via json.dumps(), missing "
                f"{json.dumps(expected_selector)!r} in expression"
            )
        side_effect.last_expr = expression
        return return_value

    side_effect.last_expr = None
    return side_effect


@pytest.mark.asyncio
async def test_type_action_password_field_redacted():
    """Type action on type='password' field returns '***' instead of actual text."""
    page = AsyncMock()
    input_domain = AsyncMock()
    runtime = AsyncMock()
    selector = 'input[name="password"]'
    runtime.evaluate.side_effect = _make_evaluator(
        {"name": "password", "autocomplete": "", "type": "password"},
        expected_selector=selector,
    )

    action = {"action": "type", "selector": selector, "text": "secret123"}

    result = await _dispatch_action(action, page, input_domain, runtime)

    assert result == 'Typed \'***\' into input[name="password"]'
    input_domain.type_text.assert_called_once_with(
        "secret123", selector='input[name="password"]'
    )
    # evaluate must be called exactly once, with a single positional arg.
    runtime.evaluate.assert_called_once()
    assert len(runtime.evaluate.call_args.args) == 1
    assert runtime.evaluate.call_args.kwargs == {}


@pytest.mark.asyncio
async def test_type_action_autocomplete_password_redacted():
    """Type action on autocomplete='password' field returns '***'."""
    page = AsyncMock()
    input_domain = AsyncMock()
    runtime = AsyncMock()
    selector = 'input[autocomplete="current-password"]'
    runtime.evaluate.side_effect = _make_evaluator(
        {"name": "current-password", "autocomplete": "current-password", "type": "text"},
        expected_selector=selector,
    )

    action = {"action": "type", "selector": selector, "text": "secret123"}

    result = await _dispatch_action(action, page, input_domain, runtime)

    assert result == 'Typed \'***\' into input[autocomplete="current-password"]'
    input_domain.type_text.assert_called_once_with(
        "secret123", selector='input[autocomplete="current-password"]'
    )
    runtime.evaluate.assert_called_once()
    assert len(runtime.evaluate.call_args.args) == 1


@pytest.mark.asyncio
async def test_type_action_name_containing_secret_redacted():
    """Type action on name containing 'secret' returns '***'."""
    page = AsyncMock()
    input_domain = AsyncMock()
    runtime = AsyncMock()
    selector = 'input[name="client_secret"]'
    runtime.evaluate.side_effect = _make_evaluator(
        {"name": "client_secret", "autocomplete": "", "type": "text"},
        expected_selector=selector,
    )

    action = {"action": "type", "selector": selector, "text": "supersecret"}

    result = await _dispatch_action(action, page, input_domain, runtime)

    assert result == 'Typed \'***\' into input[name="client_secret"]'
    input_domain.type_text.assert_called_once_with(
        "supersecret", selector='input[name="client_secret"]'
    )
    runtime.evaluate.assert_called_once()
    assert len(runtime.evaluate.call_args.args) == 1


@pytest.mark.asyncio
async def test_type_action_non_password_field_not_redacted():
    """Type action on non-password field returns full text unchanged."""
    page = AsyncMock()
    input_domain = AsyncMock()
    runtime = AsyncMock()
    selector = 'input[name="username"]'
    runtime.evaluate.side_effect = _make_evaluator(
        {"name": "username", "autocomplete": "username", "type": "text"},
        expected_selector=selector,
    )

    action = {"action": "type", "selector": selector, "text": "john_doe"}

    result = await _dispatch_action(action, page, input_domain, runtime)

    assert result == 'Typed \'john_doe\' into input[name="username"]'
    input_domain.type_text.assert_called_once_with(
        "john_doe", selector='input[name="username"]'
    )
    runtime.evaluate.assert_called_once()
    assert len(runtime.evaluate.call_args.args) == 1


@pytest.mark.asyncio
async def test_type_action_no_selector_focused_element_redacted_if_sensitive():
    """Type action with no selector (focused element) checks if focused element is sensitive."""
    page = AsyncMock()
    input_domain = AsyncMock()
    runtime = AsyncMock()
    # No selector -> the IIFE uses document.activeElement, no selector embedding.
    runtime.evaluate.side_effect = _make_evaluator(
        {"name": "pwd", "autocomplete": "current-password", "type": "password"},
    )

    action = {"action": "type", "text": "hidden"}

    result = await _dispatch_action(action, page, input_domain, runtime)

    assert result == "Typed '***' into focused element"
    input_domain.type_text.assert_called_once_with("hidden", selector=None)
    runtime.evaluate.assert_called_once()
    assert len(runtime.evaluate.call_args.args) == 1


@pytest.mark.asyncio
async def test_type_action_no_selector_focused_element_not_redacted_if_not_sensitive():
    """Type action with no selector (focused element) does not redact if not sensitive."""
    page = AsyncMock()
    input_domain = AsyncMock()
    runtime = AsyncMock()
    runtime.evaluate.side_effect = _make_evaluator(
        {"name": "email", "autocomplete": "email", "type": "text"},
    )

    action = {"action": "type", "text": "john@example.com"}

    result = await _dispatch_action(action, page, input_domain, runtime)

    assert result == "Typed 'john@example.com' into focused element"
    input_domain.type_text.assert_called_once_with("john@example.com", selector=None)
    runtime.evaluate.assert_called_once()
    assert len(runtime.evaluate.call_args.args) == 1
