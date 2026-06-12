"""
Tests for the P0-1 prelude: ConnectionState enum, WebSocketDisconnected
exception, and the env-driven tunables exported from `src.cdp`.

This is a "preflight" suite: it locks the public contract of the
state machine and the exception type so the heavier reconnect / CB
tests in later commits can rely on them.

It also re-asserts that nothing in the existing surface area was
broken by the new exports (the `from src.cdp import ...` call-sites
in the project still work).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestConnectionState:
    def test_all_states_present(self):
        from src.cdp.connection_state import ConnectionState

        values = {s.value for s in ConnectionState}
        assert values == {"healthy", "degraded", "down", "closed"}

    def test_is_terminal_helper(self):
        from src.cdp.connection_state import ConnectionState, is_terminal

        assert is_terminal(ConnectionState.DOWN) is True
        assert is_terminal(ConnectionState.CLOSED) is True
        assert is_terminal(ConnectionState.HEALTHY) is False
        assert is_terminal(ConnectionState.DEGRADED) is False

    def test_string_serialization(self):
        # str() on the enum must include the value so that structured
        # logs (json.dumps) get "healthy" / "down" / etc. — not the
        # qualified class name.
        from src.cdp.connection_state import ConnectionState

        assert ConnectionState.HEALTHY.value == "healthy"
        assert ConnectionState.DEGRADED.value == "degraded"
        assert ConnectionState.DOWN.value == "down"
        assert ConnectionState.CLOSED.value == "closed"

    def test_tunables_default_values(self, monkeypatch):
        # When the env is clean, the documented defaults must hold so
        # the behaviour is predictable without extra config.
        for var in (
            "READY_AI_CB_THRESHOLD",
            "READY_AI_CB_WINDOW_S",
            "READY_AI_CB_MAX_ATTEMPTS",
            "READY_AI_CB_BASE_S",
            "READY_AI_CB_CAP_S",
            "READY_AI_CDP_AUTORECONNECT",
        ):
            monkeypatch.delenv(var, raising=False)
        # Re-import the module so the env reads happen fresh.
        import importlib

        from src.cdp import connection_state

        importlib.reload(connection_state)
        assert connection_state.CB_THRESHOLD == 3
        assert connection_state.CB_WINDOW_S == 60.0
        assert connection_state.RECONNECT_MAX_ATTEMPTS == 5
        assert connection_state.RECONNECT_BASE_S == 0.05
        assert connection_state.RECONNECT_CAP_S == 5.0
        assert connection_state.AUTORECONNECT_ENABLED is False

    def test_tunables_env_overrides(self, monkeypatch):
        monkeypatch.setenv("READY_AI_CB_THRESHOLD", "7")
        monkeypatch.setenv("READY_AI_CB_WINDOW_S", "120")
        monkeypatch.setenv("READY_AI_CB_MAX_ATTEMPTS", "10")
        monkeypatch.setenv("READY_AI_CB_BASE_S", "0.2")
        monkeypatch.setenv("READY_AI_CB_CAP_S", "8")
        monkeypatch.setenv("READY_AI_CDP_AUTORECONNECT", "true")
        import importlib

        from src.cdp import connection_state

        importlib.reload(connection_state)
        assert connection_state.CB_THRESHOLD == 7
        assert connection_state.CB_WINDOW_S == 120.0
        assert connection_state.RECONNECT_MAX_ATTEMPTS == 10
        assert connection_state.RECONNECT_BASE_S == 0.2
        assert connection_state.RECONNECT_CAP_S == 8.0
        assert connection_state.AUTORECONNECT_ENABLED is True


class TestWebSocketDisconnected:
    def test_is_runtime_error_subclass(self):
        # Critical for backward compatibility: every existing
        # `except RuntimeError` in the codebase must still catch this.
        from src.cdp.exceptions import WebSocketDisconnected

        assert issubclass(WebSocketDisconnected, RuntimeError)
        assert WebSocketDisconnected()  # constructible with no message

    def test_carries_message(self):
        from src.cdp.exceptions import WebSocketDisconnected

        exc = WebSocketDisconnected("connection lost")
        assert str(exc) == "connection lost"
        assert isinstance(exc, RuntimeError)


class TestPublicExports:
    """Regression: nothing in `src.cdp.__init__` was broken."""

    def test_expected_symbols_exported(self):
        from src import cdp

        for name in (
            "CDPConnection",
            "PageDomain",
            "RuntimeDomain",
            "InputDomain",
            "AccessibilityDomain",
            "ConnectionState",
            "WebSocketDisconnected",
            "AUTORECONNECT_ENABLED",
        ):
            assert hasattr(cdp, name), f"missing public symbol: {name}"
            assert name in cdp.__all__, f"not in __all__: {name}"
