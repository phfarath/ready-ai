"""US8 real-browser E2E: WebMCP polyfill detection (Fase: prototype).

Loads the ``/webmcp`` fixture page (which installs a
``navigator.modelContext`` polyfill) in real headless Chrome and
asserts ``detect_model_context`` returns the page-declared tools.

Skipped automatically when no Chrome binary is present (see conftest).
"""

from __future__ import annotations

import pytest

from src.agent.loop import AgenticLoop

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_detect_polyfill_tools(e2e_server, tmp_path, cdp_port):
    loop = AgenticLoop(
        goal="e2e-webmcp",
        url=f"{e2e_server}/webmcp",
        output_dir=str(tmp_path),
        run_id="e2e-webmcp",
        headless=True,
        port=cdp_port,
        webmcp_ingest=True,
    )
    await loop._session.setup()
    try:
        await loop._session.page.navigate(f"{e2e_server}/webmcp")
        tools = await loop._session.runtime.detect_model_context()
        assert [t["name"] for t in tools] == ["shop.search", "shop.buy"]
        ctx = await loop._webmcp_context(loop._session.runtime)
        assert "shop.search" in ctx
        assert "verify on the live page" in ctx
    finally:
        await loop._session.teardown()


@pytest.mark.asyncio
async def test_absent_without_polyfill(e2e_server, tmp_path, cdp_port):
    """Pages without modelContext behave exactly as before (no tools)."""
    loop = AgenticLoop(
        goal="e2e-webmcp-absent",
        url=f"{e2e_server}/spa",
        output_dir=str(tmp_path),
        run_id="e2e-webmcp-absent",
        headless=True,
        port=cdp_port,
        webmcp_ingest=True,
    )
    await loop._session.setup()
    try:
        await loop._session.page.navigate(f"{e2e_server}/spa")
        assert await loop._session.runtime.detect_model_context() == []
        assert await loop._webmcp_context(loop._session.runtime) == ""
    finally:
        await loop._session.teardown()
