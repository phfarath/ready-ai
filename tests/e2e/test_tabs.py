"""Slice-2B real-browser E2E: real popup tabs + cross-origin actuation.

Runs declarative flows through ``AgenticLoop.run_flow`` against the local
fixture servers with real Chrome (headless). No LLM, no creds, no screenshots.

This is the TargetRegistry proving ground: ``/popup-real`` calls a REAL
``window.open``. Before 2B that hijacked the connection session (every
auto-attach replaced it); now attaches register without replacing, and the
flow drives tabs explicitly:

- click opener → wait_for_popup → switch to "/popup" → assert inside the
  popup → close it → assert back on the opener (session fell back).
- click a button INSIDE the cross-origin iframe by target reference and
  read its status back through the iframe session.

Skipped automatically when no Chrome binary is present (see conftest).
"""

from __future__ import annotations

import pytest

from src.agent.loop import AgenticLoop
from src.api.models import FlowAction, FlowAssertion, FlowSpec, FlowStepSpec

pytestmark = pytest.mark.e2e


def _loop(url: str, *, tmp_path, cdp_port: int, run_id: str) -> AgenticLoop:
    return AgenticLoop(
        goal="e2e-tabs",
        url=url,
        output_dir=str(tmp_path),
        run_id=run_id,
        headless=True,
        port=cdp_port,
    )


@pytest.mark.asyncio
async def test_real_popup_switch_close_and_fallback(e2e_server, tmp_path, cdp_port):
    flow = FlowSpec(
        name="real-popup",
        url=f"{e2e_server}/popup-real",
        steps=[
            FlowStepSpec(
                name="Open real popup",
                actions=[FlowAction(action="click", selector="#real-opener")],
                asserts=[
                    FlowAssertion(type="text_contains", expected="opened", selector="#opener-status"),
                ],
            ),
            FlowStepSpec(
                name="Wait for popup target",
                actions=[FlowAction(action="wait_for_popup")],
            ),
            FlowStepSpec(
                name="Work inside popup",
                actions=[FlowAction(action="switch_tab", target="/popup")],
                asserts=[
                    FlowAssertion(type="text_contains", expected="Popup", selector="#popup-title"),
                ],
            ),
            FlowStepSpec(
                name="Close popup",
                actions=[FlowAction(action="close_tab", target="/popup")],
            ),
            FlowStepSpec(
                name="Back on opener",
                actions=[FlowAction(action="observe")],
                asserts=[
                    FlowAssertion(type="text_contains", expected="opened", selector="#opener-status"),
                ],
            ),
        ],
    )
    loop = _loop(flow.url, tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-tabs")
    result = await loop.run_flow(flow)
    assert result["status"] == "passed", result


@pytest.mark.asyncio
async def test_cross_origin_iframe_roundtrip_via_postmessage(e2e_server, e2e_peer, tmp_path, cdp_port):
    """Real cross-origin round-trip through the live iframe.

    This Chrome does not list OOPIF iframe targets (no session to route
    to), so direct cross-session actuation has nothing to attach to here —
    the registry path stays unit-tested. What IS proven end to end: the
    parent drives the cross-origin frame via postMessage and observes its
    reply, all through the primary session. The ack element is created
    (not flipped) so the `wait` is deterministic, never a race.
    """
    flow = FlowSpec(
        name="xframe-roundtrip",
        url=f"{e2e_server}/iframe",
        steps=[
            FlowStepSpec(
                name="Ping the cross-origin frame",
                actions=[FlowAction(action="click", selector="#iframe-mirror-btn")],
                asserts=[
                    FlowAssertion(type="text_contains", expected="pinged", selector="#iframe-status-mirror"),
                ],
            ),
            FlowStepSpec(
                name="Observe the frame reply",
                actions=[FlowAction(action="wait", selector="#xframe-ack")],
                asserts=[
                    FlowAssertion(
                        type="text_contains",
                        expected="xframe:toggled",
                        selector="#xframe-ack",
                    ),
                ],
            ),
        ],
    )
    loop = _loop(flow.url, tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-xframe")
    result = await loop.run_flow(flow)
    assert result["status"] == "passed", result
