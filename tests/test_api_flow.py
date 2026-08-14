"""Tests for the declarative run-flow mode (READY-AI-T-4): API models,
flow config loader, and the POST /flows/run endpoint.

Covers:
- FlowSpec / FlowStepSpec / FlowAction / FlowAssertion / FlowExtraction models
- load_flow_config (YAML + JSON) in src/api/batch_loader.py
- POST /flows/run structured JSON result (docs-independent)
"""

import json

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock

from src.api.models import (
    FlowSpec,
    FlowAction,
    FlowRunResult,
    FlowStepResult,
    FlowActionReport,
    FlowAssertionResult,
    FlowExtractionResult,
)
from src.api.batch_loader import load_flow_config
from src.api.server import app


SAMPLE_FLOW_YAML = """
name: checkout-smoke
url: "https://app.example.com/checkout"
retries: 2
headless: true
steps:
  - name: "Open cart"
    actions:
      - action: navigate
        url: "https://app.example.com/cart"
      - action: wait
        selector: "#cart-items"
    asserts:
      - type: url_contains
        expected: "/cart"
      - type: element_visible
        selector: "#checkout-btn"
    extract:
      - name: item_count
        selector: ".cart-item"
        multiple: true
  - name: "Confirm order"
    actions:
      - action: click
        selector: "#confirm"
    asserts:
      - type: element_missing
        selector: "#spinner"
"""

FLOW_BODY = {
    "name": "checkout",
    "url": "https://app.example.com",
    "retries": 1,
    "steps": [
        {
            "name": "Open cart",
            "actions": [{"action": "observe"}],
            "asserts": [{"type": "url_contains", "expected": "/cart"}],
        }
    ],
}


class TestFlowModels:
    def test_flow_action_builds_dispatch_payload(self):
        action = FlowAction(action="click", selector="#btn", retries=2)
        payload = {
            k: v
            for k, v in action.model_dump(exclude_unset=True).items()
            if v is not None
        }
        payload.pop("retries", None)
        assert payload == {"action": "click", "selector": "#btn"}

    def test_flow_action_keeps_extra_params(self):
        action = FlowAction(action="type", selector="#user", text="alice", retries=0)
        payload = {
            k: v
            for k, v in {
                **action.model_dump(exclude_unset=True),
                **(action.model_extra or {}),
            }.items()
            if v is not None
        }
        payload.pop("retries", None)
        assert payload == {"action": "type", "selector": "#user", "text": "alice"}

    def test_flow_spec_requires_at_least_one_step(self):
        with pytest.raises(Exception):
            FlowSpec(url="https://x.example", steps=[])

    def test_flow_spec_parses_nested_document(self):
        flow = FlowSpec.model_validate(
            {
                "name": "smoke",
                "url": "https://app.example.com",
                "retries": 3,
                "steps": [
                    {
                        "name": "Step one",
                        "actions": [{"action": "navigate", "url": "https://app.example.com/a"}],
                        "asserts": [{"type": "url_contains", "expected": "/a"}],
                        "extract": [{"name": "heading", "selector": "h1"}],
                    }
                ],
            }
        )
        assert flow.name == "smoke"
        assert flow.retries == 3
        assert flow.steps[0].actions[0].action == "navigate"
        assert flow.steps[0].extract[0].name == "heading"

    def test_flow_run_result_model(self):
        result = FlowRunResult(
            run_id="flow-1",
            flow="checkout",
            url="https://x.example",
            status="failed",
            steps=[
                FlowStepResult(
                    index=1,
                    name="step",
                    actions=[
                        FlowActionReport(
                            action="click",
                            description="Clicked element: #c",
                            attempts=2,
                            passed=False,
                            failure_reason="[Failed] Element not found: #c",
                        )
                    ],
                    asserts=[
                        FlowAssertionResult(
                            type="url_contains",
                            expected="/x",
                            actual="/y",
                            passed=False,
                            message="url_contains failed",
                        )
                    ],
                    extracted=[FlowExtractionResult(name="t", selector="h1", value="Hi")],
                    attempts=2,
                    status="failed",
                    failure_reason="[Failed] Element not found: #c",
                )
            ],
            summary={"steps_total": 1, "steps_failed": 1},
        )
        data = result.model_dump()
        assert data["status"] == "failed"
        assert data["steps"][0]["actions"][0]["attempts"] == 2
        assert data["steps"][0]["asserts"][0]["actual"] == "/y"


class TestFlowLoader:
    def test_load_yaml_flow(self, tmp_path):
        path = tmp_path / "flow.yaml"
        path.write_text(SAMPLE_FLOW_YAML, encoding="utf-8")
        flow = load_flow_config(path)
        assert flow.name == "checkout-smoke"
        assert flow.url == "https://app.example.com/checkout"
        assert flow.retries == 2
        assert flow.headless is True
        assert len(flow.steps) == 2
        assert flow.steps[0].actions[0].action == "navigate"
        assert flow.steps[0].asserts[1].type == "element_visible"
        assert flow.steps[0].extract[0].multiple is True
        assert flow.steps[1].asserts[0].type == "element_missing"

    def test_load_json_flow(self, tmp_path):
        path = tmp_path / "flow.json"
        path.write_text(
            json.dumps(
                {
                    "name": "json-flow",
                    "url": "https://x.example",
                    "steps": [{"actions": [{"action": "observe"}]}],
                }
            ),
            encoding="utf-8",
        )
        flow = load_flow_config(path)
        assert flow.name == "json-flow"
        assert flow.steps[0].actions[0].action == "observe"

    def test_load_flow_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_flow_config(tmp_path / "nope.yaml")

    def test_load_flow_rejects_non_mapping(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_flow_config(path)

    def test_load_flow_rejects_unsupported_suffix(self, tmp_path):
        path = tmp_path / "flow.txt"
        path.write_text("whatever", encoding="utf-8")
        with pytest.raises(ValueError):
            load_flow_config(path)


@pytest.mark.asyncio
async def test_flows_run_endpoint_returns_structured_result(monkeypatch):
    from src.agent.loop import AgenticLoop

    fake_result = {
        "run_id": "flow-abc",
        "flow": "checkout",
        "url": "https://app.example.com",
        "status": "passed",
        "steps": [
            {
                "index": 1,
                "name": "Open cart",
                "actions": [
                    {
                        "action": "observe",
                        "params": {},
                        "description": "Observing current page state",
                        "attempts": 1,
                        "passed": True,
                        "failure_reason": "",
                    }
                ],
                "asserts": [
                    {
                        "type": "url_contains",
                        "selector": None,
                        "expected": "/cart",
                        "actual": "https://app.example.com/cart",
                        "passed": True,
                        "message": "",
                    }
                ],
                "extracted": [{"name": "item_count", "selector": ".cart-item", "value": 2}],
                "attempts": 1,
                "status": "passed",
                "failure_reason": "",
            }
        ],
        "summary": {
            "steps_total": 1,
            "steps_passed": 1,
            "steps_failed": 0,
            "actions_total": 1,
            "actions_failed": 0,
            "asserts_total": 1,
            "asserts_failed": 0,
            "extractions": 1,
            "attempts_total": 1,
            "retries_used": 0,
        },
    }
    monkeypatch.setattr(AgenticLoop, "run_flow", AsyncMock(return_value=fake_result))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/flows/run", json=FLOW_BODY)

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "flow-abc"
    assert body["status"] == "passed"
    assert body["steps"][0]["extracted"][0]["value"] == 2
    assert body["summary"]["asserts_total"] == 1


@pytest.mark.asyncio
async def test_flows_run_endpoint_returns_500_on_execution_error(monkeypatch):
    from src.agent.loop import AgenticLoop

    monkeypatch.setattr(
        AgenticLoop,
        "run_flow",
        AsyncMock(side_effect=RuntimeError("browser died")),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/flows/run", json=FLOW_BODY)

    assert response.status_code == 500
    assert "Run-flow failed" in response.json()["detail"]
