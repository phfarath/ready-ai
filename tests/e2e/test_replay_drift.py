"""PH3B e2e: drift gate trips before acting on a mutated page.

Authors a verified flow against the local ``/spa`` fixture, replays it
with a tampered manifest fingerprint (simulating a page that changed
since authoring) and proves the diverged step is never actuated while
the divergence stays observable as DRIFT_SUSPECTED.
"""

from __future__ import annotations

import pytest

from src.agent.loop import AgenticLoop
from src.agent.replay import (
    DRIFT_SUSPECTED,
    compile_manifest,
    run_replay,
)
from src.api.models import FlowAction, FlowAssertion, FlowSpec, FlowStepSpec

pytestmark = pytest.mark.e2e


def _flow(base_url: str) -> FlowSpec:
    return FlowSpec(
        name="spa-drift-source",
        url=f"{base_url}/spa",
        steps=[
            FlowStepSpec(
                name="Go to products",
                actions=[FlowAction(action="click", selector="#nav-products")],
                asserts=[
                    FlowAssertion(type="url_contains", expected="/spa/products"),
                    FlowAssertion(
                        type="text_contains",
                        expected="Products",
                        selector="#spa-status",
                    ),
                ],
            ),
        ],
    )


@pytest.mark.asyncio
async def test_drift_gate_trips_before_acting(e2e_server, tmp_path, cdp_port):
    flow = _flow(e2e_server)
    author = AgenticLoop(
        goal="e2e-drift-author",
        url=flow.url,
        output_dir=str(tmp_path),
        run_id="e2e-drift-author",
        headless=True,
        port=cdp_port,
    )
    authored = await author.run_flow(flow)
    assert authored["status"] == "passed", authored

    manifest = compile_manifest(flow, authored)
    manifest["steps"][0]["fingerprint_pre"] = "tampered-since-authoring"

    player = AgenticLoop(
        goal="e2e-drift-replay",
        url=flow.url,
        output_dir=str(tmp_path),
        run_id="e2e-drift-replay",
        headless=True,
        port=cdp_port,
    )
    result = await run_replay(
        manifest=manifest, loop=player, allow_fallback=False
    )
    assert result["status"] == "failed", result
    assert result["replay"]["drift"]["signal"] == DRIFT_SUSPECTED
    (only,) = result["steps"]
    assert only["actions"] == []  # gated before acting
    assert only["drift"]["signal"] == DRIFT_SUSPECTED
