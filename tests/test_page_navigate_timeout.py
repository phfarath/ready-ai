"""TDD — R1: `navigate` sem deadline global.

Hoje `navigate` soma até 30s de load (engolido com warning) + 10s de
idle, sem parâmetro `timeout` — uma página lenta pode segurar o step
por ~40s fora de qualquer orçamento. Fix: deadline global via
`timeout` (default 30s), com cada fase respeitando o restante.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.cdp.page import PageDomain


def _page_with_hanging_load():
    page = PageDomain.__new__(PageDomain)
    conn = AsyncMock()
    conn.send = AsyncMock(return_value={})
    conn.event_cursor = 0
    page._conn = conn
    page.context = None
    page._network_idle_cache = None
    page._network_idle_cache_ttl_s = 1.0
    page._network_enabled = False

    async def _wait_event(name, timeout, **kwargs):
        await asyncio.sleep(timeout)
        raise TimeoutError(name)

    async def _idle(**kwargs):
        await asyncio.sleep(kwargs.get("timeout", 10.0))

    page._wait_event = _wait_event
    page.wait_for_network_idle = _idle
    return page


class TestR1NavigateDeadline:
    def test_timeout_param_exists_with_default(self):
        import inspect

        sig = inspect.signature(PageDomain.navigate)
        assert "timeout" in sig.parameters
        assert sig.parameters["timeout"].default == 30.0

    @pytest.mark.asyncio
    async def test_hanging_load_respects_global_deadline(self):
        page = _page_with_hanging_load()
        start = asyncio.get_running_loop().time()
        await page.navigate("https://example.com", timeout=3.0)
        elapsed = asyncio.get_running_loop().time() - start
        assert elapsed < 5.0

    @pytest.mark.asyncio
    async def test_default_deadline_bounds_total(self):
        page = _page_with_hanging_load()
        start = asyncio.get_running_loop().time()
        await page.navigate("https://example.com")
        elapsed = asyncio.get_running_loop().time() - start
        assert elapsed < 33.0
