"""Slice-1 real-browser E2E: SPA client-side nav + Shadow DOM (Fase 1).

Runs declarative flows through ``AgenticLoop.run_flow`` against the local
fixture server with real Chrome (headless). No LLM, no creds, no screenshots.

- SPA: click a light-DOM nav button, assert client-side URL + status text.
- Shadow: click the light-DOM mirror (which drives the shadow button),
  assert the mirrored status; plus a discovery check that
  ``get_interactive_elements`` sees inside the open shadow root.

Skipped automatically when no Chrome binary is present (see conftest).
"""

from __future__ import annotations

import pytest

from src.agent.loop import AgenticLoop
from src.api.models import FlowAction, FlowAssertion, FlowSpec, FlowStepSpec

pytestmark = pytest.mark.e2e


def _loop(url: str, *, tmp_path, cdp_port: int, run_id: str) -> AgenticLoop:
    return AgenticLoop(
        goal="e2e-slice1",
        url=url,
        output_dir=str(tmp_path),
        run_id=run_id,
        headless=True,
        port=cdp_port,
    )


@pytest.mark.asyncio
async def test_spa_client_side_nav(e2e_server, tmp_path, cdp_port):
    flow = FlowSpec(
        name="spa-nav",
        url=f"{e2e_server}/spa",
        steps=[
            FlowStepSpec(
                name="Go to products",
                actions=[FlowAction(action="click", selector="#nav-products")],
                asserts=[
                    FlowAssertion(type="url_contains", expected="/spa/products"),
                    FlowAssertion(type="text_contains", expected="Products", selector="#spa-status"),
                ],
            )
        ],
    )
    loop = _loop(flow.url, tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-spa")
    result = await loop.run_flow(flow)
    assert result["status"] == "passed", result
    assert result["steps"][0]["status"] == "passed"


@pytest.mark.asyncio
async def test_shadow_mirror_interaction(e2e_server, tmp_path, cdp_port):
    flow = FlowSpec(
        name="shadow-mirror",
        url=f"{e2e_server}/shadow",
        steps=[
            FlowStepSpec(
                name="Toggle shadow via mirror",
                actions=[FlowAction(action="click", selector="#shadow-mirror-btn")],
                asserts=[
                    FlowAssertion(type="text_contains", expected="on", selector="#shadow-status-mirror"),
                ],
            )
        ],
    )
    loop = _loop(flow.url, tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-shadow")
    result = await loop.run_flow(flow)
    assert result["status"] == "passed", result


@pytest.mark.asyncio
async def test_shadow_discovery_sees_inside_open_root(e2e_server, tmp_path, cdp_port):
    """Piercing inventory: the shadow button must appear in the element list."""
    loop = _loop(f"{e2e_server}/shadow", tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-discovery")
    await loop._session.setup()
    try:
        await loop._session.page.navigate(f"{e2e_server}/shadow")
        await loop._session.page.wait_for_selector("my-card", timeout=10.0)
        elements = await loop._session.runtime.get_interactive_elements()
        assert "shadow-btn" in elements, elements[:2000]
    finally:
        await loop._session.teardown()
