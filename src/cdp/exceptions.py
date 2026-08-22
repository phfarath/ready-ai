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


class CircuitOpenError(WebSocketDisconnected):
    """Structured terminal error: the CDP circuit breaker is open.

    Raised when the FSM is in `DOWN` — the reconnect loop exhausted
    `RECONNECT_MAX_ATTEMPTS` (or `CB_THRESHOLD` failures accumulated)
    and the connection is no longer trying. It is a
    `WebSocketDisconnected` subtype, so every existing handler that
    catches the plain disconnect exception still works, but its type
    identity lets recovery coordinators treat the condition as
    terminal.

    Raised by:
      * `CDPConnection.send` / `wait_for_event` when the fail-fast
        path runs with the FSM in `DOWN` (circuit open).
      * `AgenticLoop` when the recovery budget (`MAX_CRASHES`) is
        exhausted — instead of retrying forever, the coordinator
        raises this with the connection state, the number of
        recovery attempts, and the step that was running.

    Attributes:
        state: FSM state string at failure time (usually ``"down"``).
        attempts: consecutive failures / recovery attempts observed.
        step: index of the step that was executing (loop context).
    """

    def __init__(
        self,
        message: str,
        *,
        state: str = "down",
        attempts: int = 0,
        step: int | None = None,
    ):
        super().__init__(message)
        self.state = state
        self.attempts = attempts
        self.step = step


class ChallengePageError(RuntimeError):
    """Raised when a navigation lands on a WAF/bot-challenge interstitial.

    Cloudflare, DataDome, PerimeterX and SSO captive portals answer with
    HTTP 200 and an HTML challenge (~20-40KB, sparse markup) instead of
    an error status. Trusting that document poisons every downstream
    observation, so `PageDomain.navigate` fails loud here BEFORE any
    agent state is built from it.

    Deliberately NOT a `WebSocketDisconnected` subtype: the connection is
    healthy and no heal/reconnect should fire. Handle it as a failed step
    (or pause for human solving) via the existing `except RuntimeError`
    handlers, or target it directly:

        try:
            await page.navigate(url)
        except ChallengePageError:
            # Surface "blocked by bot protection"; do not retry blindly —
            # repeated hits escalate the WAF score.
            ...

    The exception carries context only — never raw page HTML (privacy
    rule: no un-sanitized DOM leaves the CDP layer).

    Attributes:
        signature: matched challenge marker (lowercase), e.g. ``"just a moment"``.
        url: sanitized navigation target (origin/path only; query strings
          often contain credentials).
        title: document title at detection time (may be empty).
    """

    def __init__(
        self,
        message: str,
        *,
        signature: str,
        url: str = "",
        title: str = "",
    ):
        super().__init__(message)
        self.signature = signature
        self.url = url
        self.title = title
