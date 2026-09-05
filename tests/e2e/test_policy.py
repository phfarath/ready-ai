"""Slice-2A real-browser E2E: effect policy gates (Fase 2A).

Runs declarative flows through ``AgenticLoop.run_flow`` against the local
fixture server with real Chrome (headless). No LLM, no creds, no screenshots.

- ceiling: a click under a ``read`` step policy fails fail-closed with the
  ceiling in the reason, and the page is untouched (counter stays 0).
- confirmation: a ``confirm`` step reports ``pending_confirmation`` without
  executing; resuming with the key executes exactly once.
- idempotency: re-running a confirmed flow on the same loop never
  re-executes the confirmed effect (counter stays 1).

Skipped automatically when no Chrome binary is present (see conftest).
"""

from __future__ import annotations

import pytest

from src.agent.loop import AgenticLoop
from src.api.models import FlowAction, FlowAssertion, FlowSpec, FlowStepSpec

pytestmark = pytest.mark.e2e

_COUNTER_KEY = "counter-1"


def _loop(url: str, *, tmp_path, cdp_port: int, run_id: str) -> AgenticLoop:
    return AgenticLoop(
        goal="e2e-policy",
        url=url,
        output_dir=str(tmp_path),
        run_id=run_id,
        headless=True,
        port=cdp_port,
    )


def _count_assert():
    return FlowAssertion(type="text_contains", expected="0", selector="#count")


@pytest.mark.asyncio
async def test_read_ceiling_blocks_click_without_side_effect(e2e_server, tmp_path, cdp_port):
    flow = FlowSpec(
        name="policy-deny",
        url=f"{e2e_server}/counter",
        steps=[
            FlowStepSpec(
                name="No clicks under read",
                policy="read",
                actions=[FlowAction(action="click", selector="#inc-btn")],
            ),
            FlowStepSpec(
                name="Counter untouched",
                actions=[FlowAction(action="observe")],
                asserts=[_count_assert()],
            ),
        ],
    )
    loop = _loop(flow.url, tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-deny")
    result = await loop.run_flow(flow)
    assert result["status"] == "failed", result
    assert "ceiling 'read'" in result["steps"][0]["failure_reason"]
    assert result["steps"][1]["status"] == "passed"


@pytest.mark.asyncio
async def test_confirm_pending_then_resume_executes_once(e2e_server, tmp_path, cdp_port):
    flow = FlowSpec(
        name="policy-confirm",
        url=f"{e2e_server}/counter",
        steps=[
            FlowStepSpec(
                name="Guarded increment",
                confirm=True,
                idempotency_key=_COUNTER_KEY,
                actions=[FlowAction(action="click", selector="#inc-btn")],
                asserts=[
                    FlowAssertion(type="text_contains", expected="1", selector="#count"),
                ],
            )
        ],
    )
    loop = _loop(flow.url, tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-confirm")

    pending = await loop.run_flow(flow)
    assert pending["status"] == "pending_confirmation", pending
    assert pending["steps"][0]["status"] == "pending_confirmation"

    confirmed = await loop.run_flow(flow, confirm={_COUNTER_KEY})
    assert confirmed["status"] == "passed", confirmed

    # Re-running a confirmed effect never re-executes it.
    replay = await loop.run_flow(flow, confirm={_COUNTER_KEY})
    assert replay["status"] == "passed", replay
    assert replay["steps"][0].get("confirmation") == "idempotent-replay"
    assert replay["steps"][0]["actions"] == []
