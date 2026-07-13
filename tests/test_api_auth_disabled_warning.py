"""Tests for AUTH_DISABLED startup warning log (VAL-SEC-006).

When the API server starts with ``AUTH_DISABLED=true`` it MUST log a
WARNING-level message at startup so that operators are alerted that all
authentication is bypassed. No warning should be emitted when auth is
enabled.
"""
import logging

import pytest

from src.api import server

logger = logging.getLogger("src.api.server")


def test_warning_emitted_when_auth_disabled(caplog, monkeypatch):
    """A WARNING mentioning auth being disabled is logged when _AUTH_DISABLED is True."""
    monkeypatch.setattr(server, "_AUTH_DISABLED", True)

    with caplog.at_level(logging.WARNING, logger="src.api.server"):
        server._warn_if_auth_disabled()

    warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING
    ]
    assert len(warnings) >= 1, "Expected at least one WARNING log record"
    msg = warnings[0].message.lower()
    assert "auth" in msg and "disabled" in msg, (
        f"Warning message should mention auth disabled, got: {warnings[0].message!r}"
    )


def test_no_warning_when_auth_enabled(caplog, monkeypatch):
    """No WARNING about auth disabled is logged when _AUTH_DISABLED is False."""
    monkeypatch.setattr(server, "_AUTH_DISABLED", False)

    with caplog.at_level(logging.WARNING, logger="src.api.server"):
        server._warn_if_auth_disabled()

    auth_warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING
        and "auth" in r.message.lower()
        and "disabled" in r.message.lower()
    ]
    assert len(auth_warnings) == 0, (
        f"Expected no auth-disabled warning, but got: {[r.message for r in auth_warnings]}"
    )


@pytest.mark.asyncio
async def test_lifespan_calls_auth_disabled_check(mocker, monkeypatch):
    """The lifespan startup handler MUST call _warn_if_auth_disabled."""
    monkeypatch.setattr(server, "_AUTH_DISABLED", True)
    # Avoid real signal handlers in test environment
    mocker.patch.object(server.signal, "signal")

    mock_warn = mocker.patch.object(server, "_warn_if_auth_disabled")

    async with server.lifespan(server.app):
        pass

    mock_warn.assert_called_once()
