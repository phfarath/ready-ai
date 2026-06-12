"""
Public exceptions raised by the CDP layer.

These are exported from `src.cdp` so that callers (notably the
agent loop) can do targeted recovery based on the exception type
instead of pattern-matching on error strings.
"""

from __future__ import annotations


class WebSocketDisconnected(RuntimeError):
    """Raised when the CDP WebSocket is down and no reconnect is in flight.

    Subclass of `RuntimeError` so the existing `except RuntimeError`
    handlers in the codebase still catch this, but the type identity
    lets callers do targeted recovery:

        try:
            await conn.send("Page.navigate", {"url": url})
        except WebSocketDisconnected:
            # The circuit is open or the socket is gone for good.
            # Hand off to BrowserSession.recover().
            ...
        except RuntimeError:
            # A protocol-level error (method not found, bad params).
            ...

    Raised by:
      * `CDPConnection.send` when the connection is in `DOWN` or
        `CLOSED` state (fail-fast; no waiting on a timeout).
      * `CDPConnection.send` (and `wait_for_event`) when an in-flight
        command is interrupted by a connection drop.
    """
