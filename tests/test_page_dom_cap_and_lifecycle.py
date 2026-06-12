"""
Tests for PageDomain DOM-cap env and lifecycle-event wait path.

Quick wins #7 and #8 from the CDP resilience roadmap.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cdp.connection import CDPConnection
from src.cdp.page import (
    DOM_MAX_CHARS_DEFAULT,
    ENV_DOM_MAX_CHARS,
    PageDomain,
)


def _resolve() -> int:
    return PageDomain._resolve_dom_max_chars()


def _setup_page() -> tuple[PageDomain, AsyncMock]:
    conn = CDPConnection()
    conn._ws = AsyncMock()
    # DOM.getDocument returns a nodeId; DOM.getOuterHTML returns HTML.
    def fake_send(method, params=None, session_id=None, timeout=30.0):
        if method == "DOM.getDocument":
            return {"root": {"nodeId": 1}}
        if method == "DOM.getOuterHTML":
            return {"outerHTML": "<html>" + "a" * 200 + "</html>"}
        return {}
    conn.send = AsyncMock(side_effect=fake_send)
    return PageDomain(conn), conn


class TestResolveDomMaxChars:
    def test_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv(ENV_DOM_MAX_CHARS, raising=False)
        assert _resolve() == DOM_MAX_CHARS_DEFAULT

    def test_explicit_zero_means_no_cap(self, monkeypatch):
        monkeypatch.setenv(ENV_DOM_MAX_CHARS, "0")
        assert _resolve() == 0

    def test_explicit_value_honoured(self, monkeypatch):
        monkeypatch.setenv(ENV_DOM_MAX_CHARS, "16000")
        assert _resolve() == 16000

    def test_invalid_value_falls_back(self, monkeypatch):
        monkeypatch.setenv(ENV_DOM_MAX_CHARS, "garbage")
        assert _resolve() == DOM_MAX_CHARS_DEFAULT

    def test_negative_treated_as_no_cap(self, monkeypatch):
        monkeypatch.setenv(ENV_DOM_MAX_CHARS, "-1")
        assert _resolve() == 0

    def test_empty_string_falls_back(self, monkeypatch):
        monkeypatch.setenv(ENV_DOM_MAX_CHARS, "   ")
        assert _resolve() == DOM_MAX_CHARS_DEFAULT


class TestGetDomHtmlRespectsEnv:
    @pytest.mark.asyncio
    async def test_uses_default_cap(self, monkeypatch):
        monkeypatch.delenv(ENV_DOM_MAX_CHARS, raising=False)
        page, _ = _setup_page()
        out = await page.get_dom_html()
        # The 200 'a's get truncated at the default cap.
        assert len(out) <= DOM_MAX_CHARS_DEFAULT + len("\n<!-- ... truncated ... -->")

    @pytest.mark.asyncio
    async def test_uses_explicit_cap(self, monkeypatch):
        monkeypatch.setenv(ENV_DOM_MAX_CHARS, "50")
        page, _ = _setup_page()
        out = await page.get_dom_html()
        # Cap of 50 chars + the truncation marker.
        assert out.endswith("<!-- ... truncated ... -->")
        assert out.index("<html>") == 0
        assert len(out) == 50 + len("\n<!-- ... truncated ... -->")

    @pytest.mark.asyncio
    async def test_explicit_kwarg_wins(self, monkeypatch):
        monkeypatch.setenv(ENV_DOM_MAX_CHARS, "50")
        page, _ = _setup_page()
        out = await page.get_dom_html(max_length=120)
        # 120 < 200-a count, so we DO truncate.
        assert out.endswith("<!-- ... truncated ... -->")
        assert len(out) == 120 + len("\n<!-- ... truncated ... -->")


class TestLifecycleEvents:
    @pytest.mark.asyncio
    async def test_enable_calls_set_lifecycle_events(self):
        conn = CDPConnection()
        conn._ws = AsyncMock()
        conn.send = AsyncMock(return_value={})
        page = PageDomain(conn)
        await page.enable()
        assert conn.send.await_args_list
        called_methods = [c.args[0] for c in conn.send.await_args_list]
        assert "Page.setLifecycleEventsEnabled" in called_methods

    @pytest.mark.asyncio
    async def test_lifecycle_path_used_when_flag_set(self, monkeypatch):
        monkeypatch.setenv("READY_AI_USE_LIFECYCLE_EVENTS", "true")
        conn = CDPConnection()
        conn._ws = AsyncMock()
        # Page.lifecycleEvent will arrive within the wait.
        async def fake_wait_for_event(name, timeout=30.0):
            if name == "Page.lifecycleEvent":
                return {"name": "networkIdle"}
            return {}
        conn.wait_for_event = fake_wait_for_event
        # No call to send is expected on the lifecycle path.
        conn.send = AsyncMock(side_effect=AssertionError("send should not be called"))
        page = PageDomain(conn)
        # Should not raise; should return promptly.
        await page.wait_for_network_idle(timeout=1.0)

    @pytest.mark.asyncio
    async def test_lifecycle_timeout_falls_back_to_polling(self, monkeypatch):
        monkeypatch.setenv("READY_AI_USE_LIFECYCLE_EVENTS", "true")
        conn = CDPConnection()
        conn._ws = AsyncMock()

        async def fake_wait_for_event(name, timeout=30.0):
            # Simulate a stuck page: lifecycle event never arrives.
            raise TimeoutError("never")
        conn.wait_for_event = fake_wait_for_event
        conn.send = AsyncMock(return_value={})
        page = PageDomain(conn)
        # Should fall through to Network.enable + the polling loop.
        await page.wait_for_network_idle(timeout=0.5, idle_time=0.05)
        called = [c.args[0] for c in conn.send.await_args_list]
        assert "Network.enable" in called
