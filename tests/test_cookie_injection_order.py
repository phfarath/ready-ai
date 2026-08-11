"""
Tests for VAL-ROB-011: Cookie injection happens AFTER navigation.

The agentic loop must navigate to the target URL before injecting
cookies. Injecting cookies before navigate causes domain-less cookies
to be silently dropped by Chrome because no page/origin is loaded yet.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.loop import AgenticLoop


def _new_loop(tmp_path, cookies_file=None, username=None, password=None) -> AgenticLoop:
    loop = AgenticLoop(
        goal="Test cookie order",
        url="https://example.com",
        output_dir=str(tmp_path),
        headless=True,
        cookies_file=cookies_file,
        username=username,
        password=password,
    )
    # Avoid disk writes during the test.
    loop._save_checkpoint = lambda *a, **k: None
    loop._save_metrics = lambda *a, **k: None
    return loop


def _wire_session(loop: AgenticLoop, call_order: list, credentials: bool = False):
    """Replace the BrowserSession's network-touching methods with mocks that
    record their relative call order into ``call_order``."""
    session = loop._session

    session.setup = AsyncMock()
    session.teardown = AsyncMock()
    session.handle_login = AsyncMock()
    session.cookies_file = session.cookies_file
    if credentials:
        session.username = "user@example.com"
        session.password = "secret123"
    else:
        session.username = None
        session.password = None

    page = MagicMock()
    page.enable = AsyncMock()
    page.navigate = AsyncMock(side_effect=lambda *a, **k: call_order.append("navigate"))
    session._page = page

    async def _inject():
        call_order.append("inject_cookies")
    session.inject_cookies = AsyncMock(side_effect=_inject)

    return session


def _short_circuit_pipeline(loop: AgenticLoop, monkeypatch):
    """Mock out the heavy planning/execution/critic phases so run() completes."""
    monkeypatch.setattr(loop, "_resolve_steps", AsyncMock(return_value=["Step 1"]))
    monkeypatch.setattr(loop, "_execute_steps", AsyncMock(return_value=[]))
    monkeypatch.setattr(loop, "_critic_loop", AsyncMock())
    monkeypatch.setattr("src.agent.loop.save_docs", lambda *a, **k: "out.md")
    monkeypatch.setattr(loop, "_cursor", _make_stub_cursor())


def _make_stub_cursor():
    cursor = MagicMock()
    cursor.start = MagicMock()  # sync, only called when not headless
    cursor.stop = AsyncMock()
    cursor.moving = False
    return cursor


class TestCookieInjectionOrder:
    @pytest.mark.asyncio
    async def test_navigate_before_inject_cookies(self, tmp_path, monkeypatch):
        """With a cookies file, Page.navigate MUST be called before
        Network.setCookie (inject_cookies)."""
        from src.observability import init_run_context

        init_run_context("test-cookie-order-navigate-first")

        call_order: list = []
        loop = _new_loop(tmp_path, cookies_file="cookies.json")
        _wire_session(loop, call_order, credentials=False)
        _short_circuit_pipeline(loop, monkeypatch)

        await loop.run()

        assert "navigate" in call_order, "navigate was never called"
        assert "inject_cookies" in call_order, "inject_cookies was never called"
        nav_idx = call_order.index("navigate")
        inject_idx = call_order.index("inject_cookies")
        assert nav_idx < inject_idx, (
            f"navigate (index {nav_idx}) must come BEFORE inject_cookies "
            f"(index {inject_idx}). Actual order: {call_order}"
        )

    @pytest.mark.asyncio
    async def test_at_least_one_inject_after_navigate(self, tmp_path, monkeypatch):
        """With a cookies file, at least one inject_cookies call is issued
        after the first navigate."""
        from src.observability import init_run_context

        init_run_context("test-cookie-order-at-least-one")

        call_order: list = []
        loop = _new_loop(tmp_path, cookies_file="cookies.json")
        _wire_session(loop, call_order, credentials=False)
        _short_circuit_pipeline(loop, monkeypatch)

        await loop.run()

        nav_idx = call_order.index("navigate")
        injects_after = [
            i for i, name in enumerate(call_order)
            if name == "inject_cookies" and i > nav_idx
        ]
        assert len(injects_after) >= 1, (
            f"Expected at least one inject_cookies after navigate. "
            f"Order: {call_order}"
        )

    @pytest.mark.asyncio
    async def test_no_inject_cookies_without_file(self, tmp_path, monkeypatch):
        """Without a cookies file, no Network.setCookie (inject_cookies)
        is issued."""
        from src.observability import init_run_context

        init_run_context("test-cookie-order-no-file")

        call_order: list = []
        loop = _new_loop(tmp_path, cookies_file=None)
        _wire_session(loop, call_order, credentials=False)
        _short_circuit_pipeline(loop, monkeypatch)

        await loop.run()

        assert "inject_cookies" not in call_order, (
            f"inject_cookies should not be called without a cookies file. "
            f"Order: {call_order}"
        )
        assert "navigate" in call_order, "navigate should still be called"

    @pytest.mark.asyncio
    async def test_navigate_before_inject_with_credentials(self, tmp_path, monkeypatch):
        """When both cookies and credentials are present, navigate still
        happens before inject_cookies."""
        from src.observability import init_run_context

        init_run_context("test-cookie-order-with-creds")

        call_order: list = []
        loop = _new_loop(
            tmp_path,
            cookies_file="cookies.json",
            username="user@example.com",
            password="secret123",
        )
        _wire_session(loop, call_order, credentials=True)
        _short_circuit_pipeline(loop, monkeypatch)

        await loop.run()

        assert "navigate" in call_order
        assert "inject_cookies" in call_order
        nav_idx = call_order.index("navigate")
        inject_idx = call_order.index("inject_cookies")
        assert nav_idx < inject_idx, (
            f"navigate must precede inject_cookies even with credentials. "
            f"Order: {call_order}"
        )
