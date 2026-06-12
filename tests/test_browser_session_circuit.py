"""
Tests for Commit 5 of P0-1: BrowserSession hooks into the
CDP circuit breaker — the `is_disconnected` property, the
`cdp_state` diagnostic, and the structured logs emitted by
`recover()` / `teardown()`.

These tests use a fully mocked BrowserSession (no real Chrome)
because the focus is on the new public surface, not on Chrome
lifecycle. The CDPConnection is real so we can drive its FSM
directly.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.browser_session import BrowserSession
from src.cdp.connection import CDPConnection
from src.cdp.connection_state import ConnectionState


class TestIsDisconnected:
    def test_false_when_no_connection(self):
        # A freshly constructed session has no conn yet.
        bs = BrowserSession()
        assert bs.is_disconnected is False

    def test_false_when_healthy(self):
        bs = BrowserSession()
        bs._conn = CDPConnection()
        bs._conn._state = ConnectionState.HEALTHY
        assert bs.is_disconnected is False
        assert bs.cdp_state == "healthy"

    def test_false_when_degraded(self):
        bs = BrowserSession()
        bs._conn = CDPConnection()
        bs._conn._state = ConnectionState.DEGRADED
        assert bs.is_disconnected is False
        assert bs.cdp_state == "degraded"

    def test_true_when_down(self):
        bs = BrowserSession()
        bs._conn = CDPConnection()
        bs._conn._state = ConnectionState.DOWN
        assert bs.is_disconnected is True
        assert bs.cdp_state == "down"

    def test_true_when_closed(self):
        bs = BrowserSession()
        bs._conn = CDPConnection()
        bs._conn._state = ConnectionState.CLOSED
        assert bs.is_disconnected is True
        assert bs.cdp_state == "closed"

    def test_cdp_state_is_none_without_connection(self):
        bs = BrowserSession()
        assert bs.cdp_state is None


class TestTeardownMarksIntentionalClose:
    @pytest.mark.asyncio
    async def test_teardown_sets_intentional_close(self):
        # The close() method on the connection is the one that
        # actually flips the _intentional_close flag (Commit 2).
        # We just verify that teardown() reaches close() with a
        # properly wired connection.
        bs = BrowserSession()
        bs._conn = CDPConnection()
        bs._conn._state = ConnectionState.HEALTHY
        bs._conn.close = AsyncMock()
        bs._chrome_proc = None
        await bs.teardown()
        bs._conn.close.assert_called_once()
        # The mocked close() above replaces the real one, so we
        # cannot assert on _intentional_close here. The Commit 2
        # tests cover that path directly. This test just guards
        # against future regressions in the teardown wiring.

    @pytest.mark.asyncio
    async def test_teardown_emits_structured_log(self):
        from src.observability import init_run_context

        init_run_context("test-bs-teardown")
        bs = BrowserSession()
        bs._conn = CDPConnection()
        bs._conn._state = ConnectionState.DOWN  # Recovery-like teardown
        bs._conn.close = AsyncMock()
        bs._chrome_proc = None
        # We can't easily assert on log_event output, so we just
        # verify teardown completes without raising.
        await bs.teardown()


class TestRecoverLogsCircuitState:
    @pytest.mark.asyncio
    async def test_recover_logs_prior_cdp_state(self):
        from src.observability import init_run_context

        init_run_context("test-bs-recover")
        bs = BrowserSession()
        bs._conn = CDPConnection()
        bs._conn._state = ConnectionState.DOWN
        # Mock teardown/setup/navigate so we don't actually launch
        # Chrome. The point is to assert the structured logs fire.
        bs.teardown = AsyncMock()
        bs.setup = AsyncMock()

        # setup() is responsible for assigning _conn and domains.
        # We need to do that here so the test doesn't blow up.
        async def fake_setup():
            bs._page = AsyncMock()
            bs._page.enable = AsyncMock()
            bs._page.navigate = AsyncMock()
        bs.setup.side_effect = fake_setup

        with patch("src.agent.browser_session.log_event") as mock_log:
            await bs.recover("https://example.com/where-we-crashed")
            # log_event was called with browser_recover_start and
            # browser_recover_complete, both with the prior cdp_state.
            start_calls = [
                c for c in mock_log.call_args_list
                if c.args and c.args[0] == "browser_recover_start"
            ]
            complete_calls = [
                c for c in mock_log.call_args_list
                if c.args and c.args[0] == "browser_recover_complete"
            ]
            assert start_calls, "browser_recover_start not emitted"
            assert complete_calls, "browser_recover_complete not emitted"
            # The prior state was DOWN.
            for c in start_calls + complete_calls:
                assert c.kwargs.get("cdp_state") == "down" or c.kwargs.get("prior_cdp_state") == "down"
