"""
Tests for the opt-in Chrome launch flags.

P1-3 of the CDP resilience roadmap: Docker rootful environments
need --no-sandbox, and reproducible screenshots benefit from a
fixed --window-size. Both must be OFF by default and require an
explicit opt-in (env var or kwarg) to activate.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cdp import browser
from src.cdp.browser import (
    ENV_CHROME_ARGS,
    ENV_NO_SANDBOX,
    ENV_WINDOW_SIZE,
    launch_chrome,
    _parse_window_size,
    _truthy,
)


@pytest.fixture
def fake_chrome_path(tmp_path, monkeypatch):
    """Create a fake chrome binary on disk so _find_chrome_binary succeeds."""
    fake = tmp_path / "fake-chrome"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(browser, "_find_chrome_binary", lambda: str(fake))
    return fake


def _captured_args(mock_popen: MagicMock) -> list[str]:
    return list(mock_popen.call_args.args[0])


class TestHelpers:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "  1 "])
    def test_truthy(self, value):
        assert _truthy(value) is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_truthy_negative(self, value):
        assert _truthy(value) is False

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1920x1080", (1920, 1080)),
            ("1920,1080", (1920, 1080)),
            ("1366x768", (1366, 768)),
        ],
    )
    def test_parse_window_size(self, raw, expected):
        assert _parse_window_size(raw) == expected

    @pytest.mark.parametrize("raw", ["", "abc", "1920", "x1080", "1920x1080x600"])
    def test_parse_window_size_invalid(self, raw):
        assert _parse_window_size(raw) is None


class TestLaunchChromeFlags:
    @patch("subprocess.Popen")
    def test_no_flags_by_default(self, mock_popen, fake_chrome_path):
        mock_popen.return_value = MagicMock(pid=1)
        launch_chrome(port=9333, headless=True)

        args = _captured_args(mock_popen)
        # Base flags are present.
        assert "--headless=new" in args
        assert "--remote-debugging-port=9333" in args
        # New opt-in flags are ABSENT by default.
        assert "--no-sandbox" not in args
        assert not any(a.startswith("--window-size") for a in args)

    @patch("subprocess.Popen")
    def test_no_sandbox_kwarg(self, mock_popen, fake_chrome_path):
        mock_popen.return_value = MagicMock(pid=1)
        launch_chrome(port=9333, no_sandbox=True)

        assert "--no-sandbox" in _captured_args(mock_popen)

    @patch("subprocess.Popen")
    def test_no_sandbox_env(self, mock_popen, fake_chrome_path, monkeypatch):
        monkeypatch.setenv(ENV_NO_SANDBOX, "true")
        mock_popen.return_value = MagicMock(pid=1)
        launch_chrome(port=9333)

        assert "--no-sandbox" in _captured_args(mock_popen)

    @patch("subprocess.Popen")
    def test_window_size_kwarg(self, mock_popen, fake_chrome_path):
        mock_popen.return_value = MagicMock(pid=1)
        launch_chrome(port=9333, window_size=(1280, 720))

        assert "--window-size=1280,720" in _captured_args(mock_popen)

    @patch("subprocess.Popen")
    def test_window_size_env(self, mock_popen, fake_chrome_path, monkeypatch):
        monkeypatch.setenv(ENV_WINDOW_SIZE, "1024x768")
        mock_popen.return_value = MagicMock(pid=1)
        launch_chrome(port=9333)

        assert "--window-size=1024,768" in _captured_args(mock_popen)

    @patch("subprocess.Popen")
    def test_invalid_window_size_env_ignored(self, mock_popen, fake_chrome_path, monkeypatch):
        monkeypatch.setenv(ENV_WINDOW_SIZE, "garbage")
        mock_popen.return_value = MagicMock(pid=1)
        launch_chrome(port=9333)

        args = _captured_args(mock_popen)
        assert not any(a.startswith("--window-size") for a in args)

    @patch("subprocess.Popen")
    def test_extra_args_kwarg(self, mock_popen, fake_chrome_path):
        mock_popen.return_value = MagicMock(pid=1)
        launch_chrome(port=9333, extra_args=["--proxy-server=http://corp:8080"])

        assert "--proxy-server=http://corp:8080" in _captured_args(mock_popen)

    @patch("subprocess.Popen")
    def test_extra_args_env(self, mock_popen, fake_chrome_path, monkeypatch):
        monkeypatch.setenv(ENV_CHROME_ARGS, "--proxy-server=http://corp:8080 --disable-dev-shm-usage")
        mock_popen.return_value = MagicMock(pid=1)
        launch_chrome(port=9333)

        args = _captured_args(mock_popen)
        assert "--proxy-server=http://corp:8080" in args
        assert "--disable-dev-shm-usage" in args

    @patch("subprocess.Popen")
    def test_explicit_kwargs_override_env(self, mock_popen, fake_chrome_path, monkeypatch):
        # Env says no_sandbox=false, kwarg says true.
        monkeypatch.setenv(ENV_NO_SANDBOX, "false")
        mock_popen.return_value = MagicMock(pid=1)
        launch_chrome(port=9333, no_sandbox=True)

        assert "--no-sandbox" in _captured_args(mock_popen)

    @patch("subprocess.Popen")
    def test_user_data_dir_preserved(self, mock_popen, fake_chrome_path, tmp_path):
        mock_popen.return_value = MagicMock(pid=1)
        udd = str(tmp_path / "chrome-profile")
        launch_chrome(port=9333, user_data_dir=udd)

        assert f"--user-data-dir={udd}" in _captured_args(mock_popen)
