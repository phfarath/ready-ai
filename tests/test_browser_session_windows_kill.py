"""
Tests for Windows-safe Chrome process termination (VAL-ROB-001).

On Windows, signal.SIGKILL does not exist. The teardown() last-resort
path and the _kill_all_orphan_chrome atexit handler must platform-branch
to avoid AttributeError, using proc.kill() / os.kill(pid, SIGTERM) instead.
"""

import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.browser_session import (  # noqa: E402
    BrowserSession,
    _CHROME_PIDS,
    _kill_all_orphan_chrome,
)


class TestTeardownWindowsSafe:
    """teardown() must not raise AttributeError on Windows."""

    @pytest.mark.asyncio
    async def test_windows_teardown_no_attribute_error(self, mocker):
        """On win32, teardown completes without AttributeError when
        reaching the last-resort kill path."""
        mocker.patch("sys.platform", "win32")

        bs = BrowserSession()
        bs._conn = None  # skip connection close

        proc = MagicMock()
        proc.pid = 99999
        # Force every graceful path to fail so we reach last-resort
        proc.terminate.side_effect = Exception("terminate failed")
        proc.kill.side_effect = Exception("kill failed")
        proc.wait.side_effect = Exception("wait failed")
        bs._chrome_proc = proc

        # Must not raise AttributeError (or anything else)
        await bs.teardown()

        # proc.kill() was attempted on the Windows path
        proc.kill.assert_called_once()
        # Cleanup happened
        assert bs._chrome_proc is None

    @pytest.mark.asyncio
    async def test_posix_teardown_uses_sigkill(self, mocker):
        """On POSIX, the last-resort path still uses signal.SIGKILL."""
        mocker.patch("sys.platform", "linux")
        # signal.SIGKILL does not exist on Windows; create it for the test
        mocker.patch.object(signal, "SIGKILL", 9, create=True)
        mock_kill = mocker.patch("src.agent.browser_session.os.kill")

        bs = BrowserSession()
        bs._conn = None

        proc = MagicMock()
        proc.pid = 88888
        proc.terminate.side_effect = Exception("terminate failed")
        proc.kill.side_effect = Exception("kill failed")
        proc.wait.side_effect = Exception("wait failed")
        bs._chrome_proc = proc

        await bs.teardown()

        # os.kill was called with SIGKILL on POSIX
        mock_kill.assert_called_once_with(88888, 9)
        assert bs._chrome_proc is None


class TestAtexitHandlerWindowsSafe:
    """_kill_all_orphan_chrome must not crash on Windows."""

    def test_windows_atexit_no_crash(self, mocker):
        """The atexit handler completes without crash on Windows."""
        mocker.patch("sys.platform", "win32")
        mock_kill = mocker.patch("src.agent.browser_session.os.kill")

        _CHROME_PIDS.add(999999)
        try:
            # Must not raise
            _kill_all_orphan_chrome()

            # Handler completed and cleared the set
            assert len(_CHROME_PIDS) == 0
            # On Windows, os.kill is called with SIGTERM (TerminateProcess)
            mock_kill.assert_called_once_with(999999, signal.SIGTERM)
        finally:
            _CHROME_PIDS.discard(999999)

    def test_posix_atexit_uses_sigkill(self, mocker):
        """On POSIX, the atexit handler uses signal.SIGKILL."""
        mocker.patch("sys.platform", "linux")
        mocker.patch.object(signal, "SIGKILL", 9, create=True)
        mock_kill = mocker.patch("src.agent.browser_session.os.kill")

        _CHROME_PIDS.add(777777)
        try:
            _kill_all_orphan_chrome()

            assert len(_CHROME_PIDS) == 0
            mock_kill.assert_called_once_with(777777, 9)
        finally:
            _CHROME_PIDS.discard(777777)

    def test_atexit_resilient_to_oserror(self, mocker):
        """The atexit handler does not propagate OSError/AttributeError."""
        mocker.patch(
            "src.agent.browser_session.os.kill",
            side_effect=ProcessLookupError("no such process"),
        )

        _CHROME_PIDS.add(666666)
        try:
            # Must not raise even when os.kill fails
            _kill_all_orphan_chrome()
            assert len(_CHROME_PIDS) == 0
        finally:
            _CHROME_PIDS.discard(666666)
