"""Unit tests for persistent profiles and temp-profile cleanup (PH2D).

READY-AI-T-PH2D-SESSIONS DoD: temp profiles are always cleaned up (M12
leak fix); an explicit persistent dir is used as-is and never deleted.
No real Chrome is launched — launch_chrome/get_ws_url/CDPConnection are
stubbed at the browser_session boundary.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.browser_session import BrowserSession


def _stubbed_launch(monkeypatch, tmp_path):
    """Stub the Chrome-launch boundary; record the user_data_dir passed."""
    seen: dict = {}

    def fake_launch_chrome(*args, **kwargs):
        seen.update(kwargs)
        proc = MagicMock()
        proc.pid = 424242
        proc.terminate = MagicMock()
        proc.wait = MagicMock(return_value=0)
        proc.kill = MagicMock()
        return proc

    monkeypatch.setattr(
        "src.agent.browser_session.launch_chrome", fake_launch_chrome
    )
    monkeypatch.setattr(
        "src.agent.browser_session.get_ws_url",
        AsyncMock(return_value="ws://127.0.0.1:9222/devtools/browser/x"),
    )
    conn = MagicMock()
    conn.connect = AsyncMock(return_value=None)
    conn.attach_to_page = AsyncMock(return_value=None)
    conn.close = AsyncMock(return_value=None)
    conn.is_disconnected = False
    conn.reconnecting = False
    conn.state = MagicMock(value="UP")
    monkeypatch.setattr(
        "src.agent.browser_session.CDPConnection", MagicMock(return_value=conn)
    )
    return seen


@pytest.mark.asyncio
async def test_temp_profile_created_and_cleaned_up(tmp_path, monkeypatch):
    """Default session: temp dir is session-owned and removed on teardown."""
    seen = _stubbed_launch(monkeypatch, tmp_path)
    session = BrowserSession(port=19301, headless=True)
    await session.setup()
    created = seen.get("user_data_dir")
    assert created and Path(created).is_dir()
    assert session._temp_profile_dir == created
    await session.teardown()
    assert not os.path.exists(created)
    assert session._temp_profile_dir is None


@pytest.mark.asyncio
async def test_explicit_profile_dir_used_and_never_deleted(tmp_path, monkeypatch):
    """Persistent profile: passed through, preserved on teardown."""
    seen = _stubbed_launch(monkeypatch, tmp_path)
    persistent = tmp_path / "qa-profile"
    persistent.mkdir()
    (persistent / "Preferences").write_text("{}")
    session = BrowserSession(port=19302, headless=True, profile_dir=str(persistent))
    await session.setup()
    assert seen.get("user_data_dir") == str(persistent)
    assert session._temp_profile_dir is None
    await session.teardown()
    assert persistent.is_dir()
    assert (persistent / "Preferences").is_file()


@pytest.mark.asyncio
async def test_temp_profile_cleaned_when_setup_never_ran(tmp_path, monkeypatch):
    """A temp dir left behind by a failed setup is still removed."""
    _stubbed_launch(monkeypatch, tmp_path)
    orphan = tmp_path / "orphan-profile"
    orphan.mkdir()
    session = BrowserSession(port=19303, headless=True)
    session._temp_profile_dir = str(orphan)
    await session.teardown()
    assert not orphan.exists()


@pytest.mark.asyncio
async def test_recover_does_not_leak_temp_profiles(tmp_path, monkeypatch):
    """recover() tears down (cleaning the old temp dir) before respawn."""
    seen = _stubbed_launch(monkeypatch, tmp_path)
    session = BrowserSession(port=19304, headless=True)
    fake_page = MagicMock()
    fake_page.enable = AsyncMock(return_value=None)
    fake_page.navigate = AsyncMock(return_value=None)

    def _fake_domains():
        session._page = fake_page
        session._input = MagicMock()
        session._runtime = MagicMock()

    session._init_domains = _fake_domains  # type: ignore[method-assign]
    await session.setup()
    first = seen.get("user_data_dir")
    assert first and Path(first).is_dir()
    await session.recover("https://app.example.com/start")
    assert not os.path.exists(first)
    second = session._temp_profile_dir
    assert second and second != first and Path(second).is_dir()
    await session.teardown()
    assert not os.path.exists(second)


def test_profile_defaults_to_ephemeral():
    session = BrowserSession()
    assert session.profile_dir is None
    assert session._temp_profile_dir is None
