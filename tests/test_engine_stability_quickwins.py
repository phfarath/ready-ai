"""TDD — frente de estabilidade do engine, lote 1 (quick wins).

M15 — test_runner._cleanup deve usar wait(timeout) como BrowserSession.teardown
M10 — connection.close() deve separar CancelledError de Exception no reconnect
H8  — cursor não lê _ws privado; CDPConnection expõe is_alive público
M5  — extract_selector casa click_text ("Clicked element by text: 'X'")
R6  — press_key com allowlist no executor
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.cursor import extract_selector
from src.cdp.connection import CDPConnection


class TestM15CleanupUsesWait:
    @pytest.mark.asyncio
    async def test_cleanup_waits_gracefully_without_poll_sleep(self):
        from src.agent.test_runner import DocTestRunner

        proc = MagicMock()
        proc.pid = 12345
        proc.wait = MagicMock(return_value=0)
        proc.terminate = MagicMock()
        proc.kill = MagicMock()
        proc.poll = MagicMock(return_value=0)
        proc.returncode = 0

        runner = DocTestRunner(doc_path="x.md", url="http://x")
        runner._conn = None
        runner._chrome_proc = proc

        with patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock, patch(
            "src.agent.test_runner._unregister_chrome_pid"
        ):
            await runner._cleanup()

        proc.wait.assert_called()
        proc.poll.assert_not_called()
        sleep_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_kills_when_graceful_wait_fails(self):
        import subprocess

        from src.agent.test_runner import DocTestRunner

        proc = MagicMock()
        proc.pid = 12345
        proc.terminate = MagicMock()
        proc.kill = MagicMock()
        proc.wait = MagicMock(
            side_effect=[subprocess.TimeoutExpired("chrome", 2), 0]
        )

        runner = DocTestRunner(doc_path="x.md", url="http://x")
        runner._conn = None
        runner._chrome_proc = proc

        with patch("src.agent.test_runner._unregister_chrome_pid"):
            await runner._cleanup()

        proc.kill.assert_called_once()
        assert proc.wait.call_count == 2


class TestM10CloseSeparatesCancelledVsError:
    @pytest.mark.asyncio
    async def test_close_logs_reconnect_error_instead_of_swallowing(self):
        conn = CDPConnection()

        async def _raise():
            raise RuntimeError("boom-reattach")

        conn._reconnect_task = asyncio.ensure_future(_raise())
        await asyncio.sleep(0)
        with patch("src.cdp.connection.logger") as log_mock:
            await conn.close()
        assert log_mock.warning.called or log_mock.error.called or log_mock.exception.called

    @pytest.mark.asyncio
    async def test_close_cancelled_reconnect_stays_quiet(self):
        conn = CDPConnection()

        async def _cancelled():
            raise asyncio.CancelledError()

        conn._reconnect_task = asyncio.ensure_future(_cancelled())
        await asyncio.sleep(0)
        with patch("src.cdp.connection.logger") as log_mock:
            await conn.close()
        for call_name in ("error", "exception"):
            assert not getattr(log_mock, call_name).called


class TestH8PublicIsAlive:
    def test_is_alive_false_without_ws(self):
        conn = CDPConnection()
        assert conn.is_alive is False

    def test_is_alive_true_when_connected_healthy(self):
        conn = CDPConnection()
        conn._ws = MagicMock()
        assert conn.is_alive is True

    def test_is_alive_false_when_terminal(self):
        from src.cdp.connection_state import ConnectionState

        conn = CDPConnection()
        conn._ws = MagicMock()
        conn._state = ConnectionState.CLOSED
        assert conn.is_alive is False
        conn._state = ConnectionState.DOWN
        assert conn.is_alive is False

    def test_cursor_loop_does_not_touch_private_ws(self):
        import inspect

        from src.agent import cursor as cursor_mod

        src = inspect.getsource(cursor_mod.CursorAnimator._loop)
        assert "._ws" not in src
        assert "is_alive" in src


class TestM5ExtractSelectorClickText:
    def test_click_text_single_quotes(self):
        assert extract_selector("Clicked element by text: 'Entrar'") == "Entrar"

    def test_click_text_double_quotes(self):
        assert (
            extract_selector('Clicked element by text: "Submit"') == "Submit"
        )

    def test_click_text_bare(self):
        assert extract_selector("Clicked element by text: Entrar") == "Entrar"

    def test_existing_selectors_still_work(self):
        assert extract_selector("Clicked element: #login-btn") == "#login-btn"
        assert (
            extract_selector(
                'Clicked element via JS fallback: [data-testid="submit"]'
            )
            == '[data-testid="submit"]'
        )


class TestR6PressKeyAllowlist:
    @pytest.mark.asyncio
    async def test_valid_key_dispatched(self):
        from src.agent import executor

        page = MagicMock()
        runtime = MagicMock()
        input_domain = MagicMock()
        input_domain.press_key = AsyncMock()
        desc = await executor._dispatch_action(
            {"action": "press_key", "key": "Enter"}, page, input_domain, runtime
        )
        assert desc == "Pressed key: Enter"
        input_domain.press_key.assert_awaited_once_with("Enter")

    @pytest.mark.asyncio
    async def test_invalid_key_rejected_without_cdp_call(self):
        from src.agent import executor

        page = MagicMock()
        runtime = MagicMock()
        input_domain = MagicMock()
        input_domain.press_key = AsyncMock()
        desc = await executor._dispatch_action(
            {"action": "press_key", "key": "FrobnicatedKey123"},
            page,
            input_domain,
            runtime,
        )
        assert desc.startswith("[Failed]")
        input_domain.press_key.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_key_rejected(self):
        from src.agent import executor

        page = MagicMock()
        runtime = MagicMock()
        input_domain = MagicMock()
        input_domain.press_key = AsyncMock()
        desc = await executor._dispatch_action(
            {"action": "press_key"}, page, input_domain, runtime
        )
        assert desc.startswith("[Failed]")
        input_domain.press_key.assert_not_awaited()
