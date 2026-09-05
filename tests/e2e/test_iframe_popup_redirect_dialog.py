"""Slice-2 real-browser E2E: redirect, iframes, popup opener, custom dialog.

Runs declarative flows through ``AgenticLoop.run_flow`` against the local
fixture servers with real Chrome (headless). No LLM, no creds, no screenshots.

Honest scope — what the current engine can and cannot do:
- redirect: full pass (navigate follows 302, url assert verifies landing).
- same-origin iframe: *discovery* passes (inventory pierces via
  contentDocument); direct actuation inside any iframe is T-6 work.
- cross-origin iframe: parent-side mirror interaction passes; interior is
  skipped by the inventory by design (SOP) — direct actuation is T-6 work.
- popup opener: opener-half interaction passes; real window.open hijacks the
  engine session today (recv-loop replaces _session_id on every page
  attachToTarget) — tab registry is T-6 work, so the fixture does not call
  window.open and /popup is driven via direct navigation.
- dialog: custom modal passes; native alert/confirm/prompt is T-7 work.

Skipped automatically when no Chrome binary is present (see conftest).
"""

from __future__ import annotations

import pytest

from src.agent.loop import AgenticLoop
from src.api.models import FlowAction, FlowAssertion, FlowSpec, FlowStepSpec

pytestmark = pytest.mark.e2e


def _loop(url: str, *, tmp_path, cdp_port: int, run_id: str) -> AgenticLoop:
    return AgenticLoop(
        goal="e2e-slice2",
        url=url,
        output_dir=str(tmp_path),
        run_id=run_id,
        headless=True,
        port=cdp_port,
    )


@pytest.mark.asyncio
async def test_redirect_follows_to_landing(e2e_server, tmp_path, cdp_port):
    flow = FlowSpec(
        name="redirect",
        url=f"{e2e_server}/redirect",
        steps=[
            FlowStepSpec(
                name="Land after 302",
                actions=[FlowAction(action="wait", selector="#landing-status")],
                asserts=[
                    FlowAssertion(type="url_contains", expected="/landing"),
                    FlowAssertion(type="text_contains", expected="Welcome", selector="#landing-status"),
                ],
            )
        ],
    )
    loop = _loop(flow.url, tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-redirect")
    result = await loop.run_flow(flow)
    assert result["status"] == "passed", result


@pytest.mark.asyncio
async def test_same_origin_iframe_discovery(e2e_server, e2e_peer, tmp_path, cdp_port):
    """Piercing inventory reaches the same-origin iframe interior."""
    loop = _loop(f"{e2e_server}/iframe", tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-iframe-disc")
    await loop._session.setup()
    try:
        await loop._session.page.navigate(f"{e2e_server}/iframe")
        assert await loop._session.page.wait_for_selector("#same-frame", timeout=10.0)
        assert await loop._session.page.wait_for_selector("#x-frame", timeout=10.0)
        elements = await loop._session.runtime.get_interactive_elements()
        assert "inner-btn" in elements, elements[:2000]
        # Cross-origin interior is skipped by design (SOP: contentDocument
        # is null) — the inventory must not leak it, while the frame
        # itself stays addressable for T-6 actuation work.
        assert "xframe-btn" not in elements, elements[:2000]
    finally:
        await loop._session.teardown()


@pytest.mark.asyncio
async def test_cross_origin_iframe_mirror(e2e_server, e2e_peer, tmp_path, cdp_port):
    flow = FlowSpec(
        name="iframe-mirror",
        url=f"{e2e_server}/iframe",
        steps=[
            FlowStepSpec(
                name="Ping xframe via parent mirror",
                actions=[FlowAction(action="click", selector="#iframe-mirror-btn")],
                asserts=[
                    FlowAssertion(type="text_contains", expected="pinged", selector="#iframe-status-mirror"),
                ],
            )
        ],
    )
    loop = _loop(flow.url, tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-iframe")
    result = await loop.run_flow(flow)
    assert result["status"] == "passed", result


@pytest.mark.asyncio
async def test_popup_opener_and_popup_page(e2e_server, tmp_path, cdp_port):
    flow = FlowSpec(
        name="popup",
        url=f"{e2e_server}/popup-opener",
        steps=[
            FlowStepSpec(
                name="Opener interaction",
                actions=[FlowAction(action="click", selector="#opener-btn")],
                asserts=[
                    FlowAssertion(type="text_contains", expected="opened", selector="#popup-status"),
                ],
            ),
            FlowStepSpec(
                name="Popup page direct",
                actions=[FlowAction(action="navigate", url=f"{e2e_server}/popup")],
                asserts=[
                    FlowAssertion(type="text_contains", expected="Popup", selector="#popup-title"),
                ],
            ),
        ],
    )
    loop = _loop(flow.url, tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-popup")
    result = await loop.run_flow(flow)
    assert result["status"] == "passed", result


@pytest.mark.asyncio
async def test_custom_modal_accept(e2e_server, tmp_path, cdp_port):
    flow = FlowSpec(
        name="dialog",
        url=f"{e2e_server}/dialog",
        steps=[
            FlowStepSpec(
                name="Open modal",
                actions=[FlowAction(action="click", selector="#open-modal")],
                asserts=[
                    FlowAssertion(type="element_visible", selector="#modal-accept"),
                ],
            ),
            FlowStepSpec(
                name="Accept modal",
                actions=[FlowAction(action="click", selector="#modal-accept")],
                asserts=[
                    FlowAssertion(type="text_contains", expected="accepted", selector="#dialog-result"),
                ],
            ),
        ],
    )
    loop = _loop(flow.url, tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-dialog")
    result = await loop.run_flow(flow)
    assert result["status"] == "passed", result
