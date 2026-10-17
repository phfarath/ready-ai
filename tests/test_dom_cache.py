"""TDD — P1/P2 (Fase B Task 3): DOM cache por step.

Cada step/retry hoje faz 2-3 round-trips CDP (dom_html + elements +
url/fingerprint) mesmo quando a página não mudou. `DOMCache` consolida
as 3 leituras numa chave (url + fingerprint + title) com TTL curto e
invalidação explícita em ações mutantes (navigate/click/type/...).
"""

from unittest.mock import AsyncMock

import pytest

from src.agent.dom_cache import DOMCache


def _page_runtime(dom="HTML-1", elements="ELS-1", url="https://a/", fp="fp-1", title="T"):
    page = AsyncMock()
    page.get_dom_html = AsyncMock(return_value=dom)
    runtime = AsyncMock()
    runtime.get_interactive_elements = AsyncMock(return_value=elements)
    runtime.evaluate = AsyncMock(
        side_effect=lambda expr, **kw: {
            "window.location.href": url,
            "document.title": title,
            "_fp": fp,
        }.get(expr, "")
    )
    return page, runtime


def _fp(runtime, value):
    async def _eval(expr, **kw):
        if expr == "window.location.href":
            return runtime._url
        if expr == "document.title":
            return runtime._title
        return value

    runtime.evaluate = _eval


class TestDOMCache:
    @pytest.mark.asyncio
    async def test_second_get_hits_cache_without_cdp_calls(self):
        page, runtime = _page_runtime()
        runtime._url, runtime._title = "https://a/", "T"
        _fp(runtime, "fp-1")
        cache = DOMCache(ttl_s=60.0)
        first = await cache.get(page, runtime)
        page.get_dom_html.reset_mock()
        runtime.get_interactive_elements.reset_mock()
        eval_calls = []
        orig_eval = runtime.evaluate

        async def _recording_eval(expr, **kw):
            eval_calls.append(expr)
            return await orig_eval(expr, **kw)

        runtime.evaluate = _recording_eval
        second = await cache.get(page, runtime)
        assert second == first
        assert second.dom_html == "HTML-1"
        page.get_dom_html.assert_not_awaited()
        runtime.get_interactive_elements.assert_not_awaited()
        assert all("outerHTML.length" in e or e in ("window.location.href", "document.title") for e in eval_calls)

    @pytest.mark.asyncio
    async def test_fingerprint_change_misses(self):
        page, runtime = _page_runtime()
        runtime._url, runtime._title = "https://a/", "T"
        cache = DOMCache(ttl_s=60.0)
        _fp(runtime, "fp-1")
        await cache.get(page, runtime)
        _fp(runtime, "fp-2")
        state = await cache.get(page, runtime)
        assert page.get_dom_html.await_count == 2
        assert state.fingerprint == "fp-2"

    @pytest.mark.asyncio
    async def test_ttl_expiry_misses(self):
        import asyncio as _asyncio

        page, runtime = _page_runtime()
        runtime._url, runtime._title = "https://a/", "T"
        _fp(runtime, "fp-1")
        cache = DOMCache(ttl_s=0.05)
        await cache.get(page, runtime)
        await _asyncio.sleep(0.08)
        _fp(runtime, "fp-1")
        await cache.get(page, runtime)
        assert page.get_dom_html.await_count == 2

    @pytest.mark.asyncio
    async def test_invalidate_on_mutating_action(self):
        page, runtime = _page_runtime()
        runtime._url, runtime._title = "https://a/", "T"
        _fp(runtime, "fp-1")
        cache = DOMCache(ttl_s=60.0)
        await cache.get(page, runtime)
        cache.invalidate("click")
        await cache.get(page, runtime)
        assert page.get_dom_html.await_count == 2

    @pytest.mark.asyncio
    async def test_read_action_keeps_cache(self):
        page, runtime = _page_runtime()
        runtime._url, runtime._title = "https://a/", "T"
        _fp(runtime, "fp-1")
        cache = DOMCache(ttl_s=60.0)
        await cache.get(page, runtime)
        cache.invalidate("observe")
        page.get_dom_html.reset_mock()
        runtime.get_interactive_elements.reset_mock()
        await cache.get(page, runtime)
        page.get_dom_html.assert_not_awaited()
        runtime.get_interactive_elements.assert_not_awaited()

    def test_lru_eviction(self):
        cache = DOMCache(ttl_s=60.0, maxsize=2)
        cache._store["a"] = ("x", 1e9)
        cache._store["b"] = ("y", 1e9)
        cache._remember("c", "z")
        assert len(cache._store) == 2
        assert "a" not in cache._store


class TestExecutorUsesCache:
    @pytest.mark.asyncio
    async def test_retry_refresh_hits_cache_when_page_unchanged(self):
        from src.agent import executor
        from src.agent.dom_cache import DOMCache

        page = AsyncMock()
        page.get_dom_html = AsyncMock(return_value="HTML")
        page.event_cursor = 0
        page.http_failures_since = lambda cursor: []
        runtime = AsyncMock()
        runtime.get_interactive_elements = AsyncMock(return_value="ELS")
        runtime.get_state_fingerprint = AsyncMock(return_value="same")
        runtime.evaluate = AsyncMock(return_value="https://a/")
        input_domain = AsyncMock()
        llm = AsyncMock()
        llm.complete = AsyncMock(
            return_value='{"action": "observe", "expects": [{"kind": "url", "value": "https://never-there.example/"}]}'
        )
        page.wait_for_url = AsyncMock(return_value=False)
        cache = DOMCache(ttl_s=60.0)
        await cache.get(page, runtime)
        page.get_dom_html.reset_mock()
        runtime.get_interactive_elements.reset_mock()

        result = await executor.execute_step(
            "step", "HTML", "ELS", llm, page, input_domain, runtime,
            dom_cache=cache,
        )
        assert result.success is False
        page.get_dom_html.assert_not_awaited()
        runtime.get_interactive_elements.assert_not_awaited()
