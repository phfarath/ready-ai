"""
Connection lifecycle states for CDPConnection.

P0-1 of the CDP resilience roadmap. Until now, the only signal
that something was wrong with the WebSocket was a 30s timeout on
`send`, which forced the orchestrator to tear down and respawn
Chrome for every transient hiccup. This module introduces an
explicit finite state machine so the connection can transition
between healthy, mid-reconnect, circuit-open, and intentionally
closed — and so callers can ask `is_disconnected` without timing
out a command.

All thresholds are env-driven so ops can tune them without
redeploying. Values are read at import time (this is a small
module, the cost of a re-import is zero).
"""

from __future__ import annotations

import os
from enum import Enum


class ConnectionState(str, Enum):
    """Lifecycle states for a CDP WebSocket connection.

    `HEALTHY`     — socket is open and we have not seen failures.
    `DEGRADED`    — socket is down; a background reconnect is in flight.
    `DOWN`        — circuit breaker is open; callers should treat
                    every `send` as failing fast.
    `CLOSED`      — `close()` was called explicitly (orchestrator
                    teardown). We will NOT attempt to reconnect.
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    CLOSED = "closed"


# ---------------------------------------------------------------------------
# Tunables (all env-driven)
# ---------------------------------------------------------------------------

# How many consecutive failures inside CB_WINDOW_S open the circuit.
CB_THRESHOLD: int = int(os.environ.get("READY_AI_CB_THRESHOLD", "3"))

# Sliding-window size for the consecutive-failure counter.
CB_WINDOW_S: float = float(os.environ.get("READY_AI_CB_WINDOW_S", "60"))

# Max reconnect attempts before the circuit is forced open.
RECONNECT_MAX_ATTEMPTS: int = int(os.environ.get("READY_AI_CB_MAX_ATTEMPTS", "5"))

# Initial backoff delay for reconnect (seconds); doubles each attempt
# until RECONNECT_CAP_S, with a ±10% jitter applied on top.
RECONNECT_BASE_S: float = float(os.environ.get("READY_AI_CB_BASE_S", "0.05"))
RECONNECT_CAP_S: float = float(os.environ.get("READY_AI_CB_CAP_S", "5.0"))

# Re-attach strategy: how long to wait for the auto-attach event
# before falling back to a manual `Target.attachToTarget`.
REATTACH_AUTO_WAIT_S: float = float(os.environ.get("READY_AI_CB_REATTACH_WAIT_S", "3.0"))

# Master switch: the whole reconnect/CB machinery is opt-in. When
# false, the legacy behaviour is preserved (a WS drop kills the recv
# loop and forces BrowserSession.recover).
AUTORECONNECT_ENABLED: bool = os.environ.get(
    "READY_AI_CDP_AUTORECONNECT", ""
).lower() in ("1", "true", "yes", "on")


def is_terminal(state: ConnectionState) -> bool:
    """True if no more reconnect attempts will be made from this state."""
    return state in (ConnectionState.DOWN, ConnectionState.CLOSED)
