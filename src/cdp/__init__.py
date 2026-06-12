from .connection import CDPConnection
from .browser import launch_chrome, get_ws_url
from .page import PageDomain
from .input import InputDomain
from .runtime import RuntimeDomain
from .accessibility import AccessibilityDomain, get_ax_snapshot
from .connection_state import (
    AUTORECONNECT_ENABLED,
    CB_THRESHOLD,
    CB_WINDOW_S,
    RECONNECT_BASE_S,
    RECONNECT_CAP_S,
    RECONNECT_MAX_ATTEMPTS,
    ConnectionState,
    is_terminal,
)
from .exceptions import WebSocketDisconnected

__all__ = [
    "CDPConnection",
    "launch_chrome",
    "get_ws_url",
    "PageDomain",
    "InputDomain",
    "RuntimeDomain",
    "AccessibilityDomain",
    "get_ax_snapshot",
    "ConnectionState",
    "is_terminal",
    "WebSocketDisconnected",
    # Constants (read at import time; useful for diagnostics/tests).
    "AUTORECONNECT_ENABLED",
    "CB_THRESHOLD",
    "CB_WINDOW_S",
    "RECONNECT_BASE_S",
    "RECONNECT_CAP_S",
    "RECONNECT_MAX_ATTEMPTS",
]
