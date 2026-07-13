"""
Tests for VAL-ROB-005: recover() attempts handle_login when credentials are present.

After a crash, recover() should call handle_login(llm) when username and
password are set, instead of silently skipping login and relying on
possibly-expired cookies.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.browser_session import BrowserSession
from src.cdp.connection import CDPConnection
from src.cdp.connection_state import ConnectionState


def _make_session(**kwargs):
    """Build a BrowserSession with a DOWN connection and all recover()
    dependencies mocked so no real Chrome is launched."""
    bs = BrowserSession(**kwargs)
    bs._conn = CDPConnection()
    bs._conn._state = ConnectionState.DOWN
    bs.teardown = AsyncMock()
    bs.setup = AsyncMock()
    bs._page = AsyncMock()
    bs._page.enable = AsyncMock()
    bs._page.navigate = AsyncMock()
    bs.handle_login = AsyncMock()
    return bs


class TestRecoverLogin:
    @pytest.mark.asyncio
    async def test_recover_calls_handle_login_with_credentials(self):
        """With credentials set, recover() calls handle_login exactly once."""
        from src.observability import init_run_context

        init_run_context("test-recover-login-creds")
        bs = _make_session(username="user@example.com", password="secret123")
        llm_mock = AsyncMock()

        await bs.recover("https://example.com/crashed-page", llm_mock)

        bs.handle_login.assert_called_once_with(llm_mock)

    @pytest.mark.asyncio
    async def test_recover_no_handle_login_without_credentials(self):
        """Without credentials, handle_login is NOT called."""
        from src.observability import init_run_context

        init_run_context("test-recover-login-nocreds")
        bs = _make_session()  # no username/password
        llm_mock = AsyncMock()

        await bs.recover("https://example.com/crashed-page", llm_mock)

        bs.handle_login.assert_not_called()

    @pytest.mark.asyncio
    async def test_recover_still_navigates_with_credentials(self):
        """recover() still navigates to the target URL when credentials are set."""
        from src.observability import init_run_context

        init_run_context("test-recover-login-navigate")
        bs = _make_session(username="user@example.com", password="secret123")
        llm_mock = AsyncMock()

        await bs.recover("https://example.com/crashed-page", llm_mock)

        bs._page.navigate.assert_called_once_with(
            "https://example.com/crashed-page", wait_for_network=True
        )

    @pytest.mark.asyncio
    async def test_recover_no_llm_no_handle_login_with_credentials(self):
        """With credentials but no llm passed, handle_login is not called
        (backwards-compatible default behaviour for callers without an llm)."""
        from src.observability import init_run_context

        init_run_context("test-recover-login-no-llm")
        bs = _make_session(username="user@example.com", password="secret123")

        await bs.recover("https://example.com/crashed-page")

        bs.handle_login.assert_not_called()
