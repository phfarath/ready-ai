"""T-2 executor tests for typed expectations and passive HTTP failures."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.executor import _parse_expectations, _verify_expectations, execute_step


def test_parse_typed_expectations():
    values = _parse_expectations(
        {"expects": [{"kind": "element", "selector": "#save", "state": "enabled"}]}
    )
    assert values[0].kind == "element"
    assert values[0].state == "enabled"


@pytest.mark.asyncio
async def test_verify_network_expectation_records_sanitized_evidence():
    page = MagicMock()
    page.wait_for_http = AsyncMock(
        return_value=MagicMock(passed=True, observed="HTTP 201", details={"status": 201, "url": "https://x/create"})
    )
    expectations = _parse_expectations({"expect": {"kind": "network", "status": 201}})
    evidence = await _verify_expectations(page, expectations, after_sequence=3)
    assert evidence[0].passed is True
    assert evidence[0].details["status"] == 201


@pytest.mark.asyncio
async def test_executor_rejects_http_500_even_when_ui_changed():
    page = MagicMock()
    page.event_cursor = 7
    page.wait_for_navigation_settled = AsyncMock(return_value=False)
    page.http_failures_since = MagicMock(
        return_value=[MagicMock(kind="network_http", passed=False, observed="HTTP 500", details={"status": 500, "url": "https://app.test/save"})]
    )
    page.get_dom_html = AsyncMock(return_value="<html></html>")
    runtime = MagicMock()
    runtime.get_state_fingerprint = AsyncMock(return_value="changed")
    runtime.evaluate = AsyncMock(return_value="https://app.test")
    runtime.get_interactive_elements = AsyncMock(return_value="[]")
    input_domain = MagicMock()
    llm = MagicMock()
    with patch("src.agent.executor._get_action", AsyncMock(return_value={"action": "observe"})), patch(
        "src.agent.executor._dispatch_action", AsyncMock(return_value="Observed UI changed")
    ):
        result = await execute_step("save", "<html></html>", "[]", llm, page, input_domain, runtime)
    assert result.success is False
    assert "HTTP failure status 500" in result.failure_reason
    assert any(item.details.get("status") == 500 for item in result.evidence)
