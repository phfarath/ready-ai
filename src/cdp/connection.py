"""
CDP WebSocket Connection Manager.

Handles raw JSON-RPC communication with Chrome DevTools Protocol over WebSocket.
Auto-incrementing message IDs, session-aware messaging, and event listening.
"""

import asyncio
import json
import logging
import random
import time
from typing import Any, Optional

import websockets
from websockets.asyncio.client import ClientConnection

from ..observability import Span, get_metrics
from .connection_state import (
    AUTORECONNECT_ENABLED,
    CB_THRESHOLD,
    CB_WINDOW_S,
    RECONNECT_BASE_S,
    RECONNECT_CAP_S,
    RECONNECT_MAX_ATTEMPTS,
    REATTACH_AUTO_WAIT_S,
    ConnectionState,
)
from .exceptions import CircuitOpenError, WebSocketDisconnected

logger = logging.getLogger(__name__)


class CDPConnection:
    """Low-level CDP WebSocket connection with auto-incrementing IDs."""

    def __init__(self):
        self._ws: Optional[ClientConnection] = None
        self._msg_id: int = 0
        self._session_id: Optional[str] = None
        self._pending: dict[int, asyncio.Future] = {}
        self._events: asyncio.Queue = asyncio.Queue()
        self._recv_task: Optional[asyncio.Task] = None

        # P0-1 reconnect machinery. Fields are added here so the rest of
        # the class can rely on them being present (and so tests can
        # poke at them in isolation). Behaviour is gated by
        # AUTORECONNECT_ENABLED — when that flag is off, all of these
        # fields exist but stay at their initial values and the legacy
        # code path runs unchanged.
        self._state: ConnectionState = ConnectionState.HEALTHY
        self._state_lock: asyncio.Lock = asyncio.Lock()
        self._consecutive_failures: int = 0
        self._first_failure_ts: Optional[float] = None
        self._ws_url: Optional[str] = None
        self._target_id: Optional[str] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._disconnect_event: asyncio.Event = asyncio.Event()
        # Set by `close()` so the recv loop can distinguish
        # orchestrator-driven teardown (no reconnect) from a genuine
        # socket drop (reconnect allowed).
        self._intentional_close: bool = False
        # Signalled when an in-flight wait_for_event should bail out
        # because the socket is gone. The flag is sticky: callers that
        # arrived after the disconnect will see it set immediately.
        self._abort_wait: asyncio.Event = asyncio.Event()

    async def connect(self, ws_url: str) -> None:
        """Establish WebSocket connection to CDP endpoint."""
        logger.info(f"Connecting to {ws_url}")
        # Cache the URL for the reconnect loop in Commit 3.
        self._ws_url = ws_url
        # P0-1: enable native ping/heartbeat. The websockets library
        # sends an unsolicited ping every `ping_interval` seconds and
        # closes the socket if no pong arrives within `ping_timeout`.
        # This gives us fast failure detection (≈30s in the worst
        # case) without us having to maintain our own ping loop.
        self._ws = await websockets.connect(
            ws_url,
            max_size=50 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        )
        # A fresh connection is healthy by definition.
        self._state = ConnectionState.HEALTHY
        self._abort_wait.clear()
        self._recv_task = asyncio.create_task(self._recv_loop())
        logger.info("CDP connection established")

    async def _recv_loop(self) -> None:
        """Background loop that routes incoming messages to pending futures or event queue."""
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                msg_id = msg.get("id")

                if msg_id is not None and msg_id in self._pending:
                    # Response to a sent command
                    self._pending[msg_id].set_result(msg)
                    del self._pending[msg_id]
                elif "method" in msg:
                    # Monitor Target auto-attach to heal dead sessions
                    if msg["method"] == "Target.attachedToTarget":
                        target_info = msg.get("params", {}).get("targetInfo", {})
                        if target_info.get("type") == "page":
                            new_session = msg.get("params", {}).get("sessionId")
                            if new_session:
                                logger.debug(f"Auto-attached to new page target: {target_info.get('targetId')}, session: {new_session}")
                                self._session_id = new_session
                                # Re-enable required CDP domains and re-inject
                                # the cursor overlay on the new session —
                                # without this, Page.loadEventFired never fires
                                # and Runtime.evaluate can hang against an
                                # unprepared context. Must be a background
                                # task: we can't await responses from inside
                                # the recv loop or we deadlock.
                                asyncio.create_task(
                                    self._post_attach_enable(new_session)
                                )

                    # CDP event (e.g., Page.loadEventFired)
                    await self._events.put(msg)
                else:
                    # Unmatched message — could be a response to an unknown ID
                    logger.debug(f"Unmatched CDP message: {json.dumps(msg)[:200]}")
        except websockets.exceptions.ConnectionClosed:
            logger.warning("CDP WebSocket connection closed")
            await self._handle_disconnect(intentional=self._intentional_close)
        except Exception as e:
            logger.error(f"CDP recv loop error: {e}")
            await self._handle_disconnect(intentional=self._intentional_close)

    async def _handle_disconnect(self, intentional: bool) -> None:
        """Common path for any socket-down condition.

        Three responsibilities, in order:
          1. Mark the FSM (HEALTHY -> DEGRADED, or -> CLOSED if
             intentional). Locked so concurrent `send` calls see a
             consistent state. Also bump the consecutive-failure
             counter (when unintentional) and, if the sliding
             window has accumulated >= CB_THRESHOLD failures,
             open the circuit (DEGRADED -> DOWN).
          2. Drain in-flight `send`/`wait_for_event` waiters with a
             WebSocketDisconnected exception so they don't sit on a
             dead future for 30s.
          3. Schedule a background reconnect task — but only when
             auto-reconnect is enabled AND the close was not
             intentional AND the circuit is still closed. The
             reconnect task itself is wired in Commit 3; once
             RECONNECT_MAX_ATTEMPTS is exhausted there, the FSM
             also moves to DOWN.

        This method is safe to call multiple times: subsequent
        invocations accumulate into the failure counter even when
        the state is already DEGRADED (reconnects can fail repeatedly
        and the counter must still climb).
        """
        circuit_opened_here = False
        async with self._state_lock:
            if intentional:
                # Orchestrator-driven shutdown. We do not reconnect.
                if self._state != ConnectionState.CLOSED:
                    self._state = ConnectionState.CLOSED
                    logger.info("CDP connection closed intentionally")
            else:
                # Genuine drop. Move HEALTHY -> DEGRADED on the first
                # failure; stay in DEGRADED on subsequent failures
                # (the counter still climbs so the circuit can open).
                if self._state == ConnectionState.HEALTHY:
                    self._state = ConnectionState.DEGRADED
                # Sliding-window counter. If the window from the
                # first failure has elapsed, we reset the counter
                # so a single old failure does not poison today's
                # budget.
                now = time.monotonic()
                if (
                    self._first_failure_ts is not None
                    and (now - self._first_failure_ts) > CB_WINDOW_S
                ):
                    self._consecutive_failures = 0
                    self._first_failure_ts = None
                if self._first_failure_ts is None:
                    self._first_failure_ts = now
                self._consecutive_failures += 1
                # Open the circuit if the budget is spent.
                if (
                    self._state != ConnectionState.DOWN
                    and self._consecutive_failures >= CB_THRESHOLD
                ):
                    self._state = ConnectionState.DOWN
                    circuit_opened_here = True
                    logger.error(
                        f"CDP circuit breaker OPEN after "
                        f"{self._consecutive_failures} consecutive "
                        f"failures in {CB_WINDOW_S}s window"
                    )

        # Drain pending commands so callers don't sit on dead futures.
        self._drain_pending()
        # Signal any in-flight wait_for_event to bail out.
        self._abort_wait.set()

        # Observability.
        metrics = get_metrics()
        if metrics is not None:
            metrics.increment("cdp.disconnects", intentional=intentional)
            if circuit_opened_here:
                metrics.increment("cdp.circuit.opens")

        # Reconnect scheduling is wired in Commit 3. The flag check
        # lives here so that as soon as that commit lands, the
        # behaviour activates without further changes to this method.
        # We do NOT schedule a reconnect when the circuit just opened
        # — the user has decided to bail out, so we let the
        # orchestrator decide whether to call BrowserSession.recover().
        if (
            not intentional
            and AUTORECONNECT_ENABLED
            and self._ws_url is not None
            and self._reconnect_task is None
            and not circuit_opened_here
        ):
            self._reconnect_task = asyncio.create_task(self._reconnect())
            logger.debug("CDP reconnect task scheduled")

    def _drain_pending(self) -> None:
        """Wake every in-flight `send` with WebSocketDisconnected.

        Called from `_handle_disconnect` so that commands sent just
        before the socket died don't have to wait for their own
        30s timeout to learn about the failure.
        """
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(
                    WebSocketDisconnected("CDP WebSocket closed")
                )
        self._pending.clear()

    def _disconnect_error(self, message: str) -> WebSocketDisconnected:
        """Pick the exception type for a failed CDP interaction.

        When the FSM is `DOWN` (circuit breaker open, no reconnect in
        flight) a structured `CircuitOpenError` is raised so recovery
        coordinators can treat the condition as terminal; every other
        state (`DEGRADED` mid-drop, `CLOSED` intentional teardown)
        keeps the plain `WebSocketDisconnected`. Compared by value
        (==) so tests that reload the state module keep working.
        """
        if self._state == ConnectionState.DOWN:
            return CircuitOpenError(
                message,
                state=ConnectionState.DOWN.value,
                attempts=self._consecutive_failures,
            )
        return WebSocketDisconnected(message)

    def _release_reconnect_slot(self) -> None:
        """Drop the finished reconnect task reference (READY-AI-T-3/Q2).

        ``_handle_disconnect`` only schedules a reconnect while
        ``self._reconnect_task is None``. If the completed task kept
        its own reference, a SECOND disconnect in the same session
        could never schedule a fresh reconnect task — the headline
        reattach path would work exactly once per session. Called
        from every ``_reconnect`` exit (success, exhaustion, and the
        abort paths) so the guard-by-identity semantics stay
        consistent everywhere.
        """
        self._reconnect_task = None

    async def _reconnect(self) -> None:
        """Background reconnect loop with exponential backoff and jitter.

        Runs as a task spawned by `_handle_disconnect`. We attempt up
        to `RECONNECT_MAX_ATTEMPTS` reconnects with delays of
        `min(RECONNECT_CAP_S, RECONNECT_BASE_S * 2**attempt) + jitter`.

        On success we re-establish the WS, re-attach to the original
        target (auto-attach with 3s timeout, manual attach as a
        fallback), re-enable Page/DOM/Runtime, and re-inject the
        cursor overlay. The FSM is then moved back to HEALTHY and
        `_abort_wait` is cleared so any new `wait_for_event` calls
        can run normally.

        On exhaustion (all attempts failed) the FSM is moved to
        DOWN, the disconnect event is signalled, and the circuit
        breaker machinery in Commit 4 will see the consecutive
        failures and decide whether to keep the circuit open.

        Every exit path releases its own task slot (see
        `_release_reconnect_slot`) so a future disconnect can spawn a
        fresh reconnect.
        """
        if not self._ws_url:
            async with self._state_lock:
                self._state = ConnectionState.DOWN
            self._disconnect_event.set()
            logger.error("reconnect aborted: no cached ws_url")
            self._release_reconnect_slot()
            return

        metrics = get_metrics()
        last_error: Optional[Exception] = None
        for attempt in range(RECONNECT_MAX_ATTEMPTS):
            # Stop early if the orchestrator tore us down while we
            # were sleeping in the backoff.
            if self._state == ConnectionState.CLOSED:
                logger.info("reconnect aborted: connection was closed")
                self._release_reconnect_slot()
                return

            delay = min(RECONNECT_CAP_S, RECONNECT_BASE_S * (2 ** attempt))
            delay += random.uniform(0, delay * 0.1)  # ±10% jitter
            logger.debug(
                f"reconnect attempt {attempt + 1}/{RECONNECT_MAX_ATTEMPTS} "
                f"after {delay:.2f}s"
            )
            await asyncio.sleep(delay)

            try:
                new_ws = await websockets.connect(
                    self._ws_url,
                    max_size=50 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                )
                # Replace the socket BEFORE re-attaching so subsequent
                # sends (the reattach path uses them) hit the new
                # connection.
                self._ws = new_ws
                self._recv_task = asyncio.create_task(self._recv_loop())
                await self._post_reconnect_reattach()

                async with self._state_lock:
                    self._state = ConnectionState.HEALTHY
                    self._consecutive_failures = 0
                    self._first_failure_ts = None
                # Clear the abort flag so future wait_for_event calls
                # can run normally.
                self._abort_wait.clear()
                if metrics is not None:
                    metrics.increment("cdp.reconnect.attempts", outcome="success")
                logger.info(f"CDP reconnected after {attempt + 1} attempt(s)")
                self._release_reconnect_slot()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(f"reconnect attempt {attempt + 1} failed: {exc}")
                if metrics is not None:
                    metrics.increment("cdp.reconnect.attempts", outcome="failure")
                continue

        # Exhausted.
        async with self._state_lock:
            self._state = ConnectionState.DOWN
        self._disconnect_event.set()
        if metrics is not None:
            metrics.increment("cdp.reconnect.exhausted")
        logger.error(
            f"CDP reconnect failed after {RECONNECT_MAX_ATTEMPTS} attempts; "
            f"circuit OPEN. Last error: {last_error}"
        )
        self._release_reconnect_slot()

    async def _post_reconnect_reattach(self) -> None:
        """Re-attach to the page target after a successful reconnect.

        Two-step strategy (híbrido from the plan):
          1. Wait up to REATTACH_AUTO_WAIT_S for Chrome to emit
             `Target.attachedToTarget` via the auto-attach machinery
             we re-enable in step 3. The recv loop picks this up and
             updates `self._session_id`.
          2. If no auto-attach arrives in time, fall back to a
             manual `Target.attachToTarget` with the cached
             `_target_id`.
          3. In all cases, re-enable Page/DOM/Runtime and re-inject
             the cursor script on the new session. Without this,
             Page.loadEventFired never fires and Runtime.evaluate
             can hang against an unprepared context.
        """
        # Step 1: clear session_id; the recv loop will set it if
        # Chrome sends Target.attachedToTarget in the next few
        # seconds.
        self._session_id = None
        deadline = asyncio.get_running_loop().time() + REATTACH_AUTO_WAIT_S
        while asyncio.get_running_loop().time() < deadline and not self._session_id:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                evt = await asyncio.wait_for(
                    self._events.get(), timeout=min(remaining, 0.2)
                )
                # Re-queue non-attach events so they can be consumed
                # by the next waiter.
                if evt.get("method") != "Target.attachedToTarget":
                    await self._events.put(evt)
                    # Avoid a tight loop yielding back-to-back.
                    await asyncio.sleep(0)
            except asyncio.TimeoutError:
                break

        # Step 2: fallback manual attach using the cached target_id.
        if not self._session_id and self._target_id:
            try:
                result = await self.send(
                    "Target.attachToTarget",
                    {"targetId": self._target_id, "flatten": True},
                    timeout=5.0,
                )
                self._session_id = result.get("sessionId")
                # Re-enable auto-attach for future cross-origin swaps.
                await self.send(
                    "Target.setAutoAttach",
                    {
                        "autoAttach": True,
                        "waitForDebuggerOnStart": False,
                        "flatten": True,
                    },
                    timeout=5.0,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"re-attach fallback failed: {exc}")
                raise

        # Step 3: re-enable the essential domains and the cursor.
        if self._session_id:
            for method in ("Page.enable", "DOM.enable", "Runtime.enable"):
                try:
                    await self.send(
                        method, session_id=self._session_id, timeout=5.0
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"re-enable {method} failed: {exc}")
            # Re-register the cursor overlay. Imported lazily to avoid
            # a circular import between page <-> connection.
            try:
                from .page import register_cursor_script

                await register_cursor_script(self, session_id=self._session_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"cursor re-injection failed: {exc}")
        else:
            # We reconnected but could not attach to a page target.
            # Raise so the reconnect loop counts this attempt as a
            # failure and tries again.
            raise RuntimeError("re-attach did not yield a session_id")

    async def _post_attach_enable(self, session_id: str) -> None:
        """Re-enable Page/DOM/Runtime and re-inject the cursor script on a
        freshly auto-attached session so events fire and the visual overlay
        survives cross-origin process swaps."""
        for method in ("Page.enable", "DOM.enable", "Runtime.enable"):
            try:
                await self.send(method, session_id=session_id, timeout=5.0)
            except Exception as e:
                logger.debug(
                    f"post-attach {method} on {session_id} failed: {e}"
                )
        # Re-register the cursor/highlight overlay on the new session.
        # Imported lazily to avoid a circular import between page <-> connection.
        try:
            from .page import register_cursor_script
            await register_cursor_script(self, session_id=session_id)
        except Exception as e:
            logger.debug(f"post-attach cursor re-injection failed: {e}")

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def send(
        self,
        method: str,
        params: Optional[dict] = None,
        session_id: Optional[str] = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """
        Send a CDP command and wait for its response.

        Args:
            method: CDP method name (e.g., 'Page.navigate')
            params: Optional parameters dict
            session_id: Optional session ID for target-scoped commands
            timeout: Max seconds to wait for response

        Returns:
            The CDP response dict (contains 'result' or 'error')
        """
        if self._ws is None:
            raise RuntimeError("Not connected. Call connect() first.")

        # P0-1: fail-fast on terminal states. DOWN means the circuit
        # breaker is open; CLOSED means the orchestrator already
        # tore us down. Either way, do not even enqueue a command —
        # callers should treat the situation as 'go through
        # BrowserSession.recover()' rather than retrying in a loop.
        # READY-AI-T-3: an open circuit raises the structured
        # CircuitOpenError so the recovery coordinator can spot the
        # terminal condition by type (still a WebSocketDisconnected,
        # so existing handlers keep working).
        if self._state in (ConnectionState.DOWN, ConnectionState.CLOSED):
            raise self._disconnect_error(
                f"CDP connection is {self._state.value}; not sending {method}"
            )

        msg_id = self._next_id()
        message: dict[str, Any] = {"id": msg_id, "method": method}

        if params:
            message["params"] = params
        if session_id:
            message["sessionId"] = session_id
        elif self._session_id:
            message["sessionId"] = self._session_id

        # Create a future for this response
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[msg_id] = future

        logger.debug(f"CDP send [{msg_id}]: {method}")

        # Observability: span over the full RTT so the latency histogram
        # captures both the network send and the response wait. We use
        # the synchronous Span context manager and time it with
        # time.monotonic() for the histogram — get_metrics() can return
        # None when no RunContext is active, so we degrade gracefully.
        start = time.monotonic()
        status = "ok"
        metrics = get_metrics()
        with Span(
            f"cdp.{method}",
            attributes={"method": method, "msg_id": msg_id},
        ):
            try:
                await self._ws.send(json.dumps(message))
                try:
                    result = await asyncio.wait_for(future, timeout=timeout)
                except asyncio.TimeoutError:
                    status = "timeout"
                    self._pending.pop(msg_id, None)
                    raise TimeoutError(
                        f"CDP command {method} (id={msg_id}) timed out after {timeout}s"
                    )

                if "error" in result:
                    err = result["error"]
                    status = "error"
                    raise RuntimeError(
                        f"CDP error [{err.get('code')}]: {err.get('message')}"
                    )
                return result.get("result", {})
            except Exception:
                if status == "ok":
                    status = "error"
                # Clean up the pending future so it doesn't leak when
                # _ws.send raises (timeout path already pops above).
                self._pending.pop(msg_id, None)
                raise
            finally:
                elapsed_ms = (time.monotonic() - start) * 1000.0
                if metrics is not None:
                    metrics.record("cdp.latency_ms", elapsed_ms)
                    metrics.increment(
                        "cdp.commands", method=method, status=status
                    )

    async def wait_for_event(
        self, event_name: str, timeout: float = 30.0
    ) -> dict[str, Any]:
        """
        Wait for a specific CDP event.

        Non-matching events are buffered and re-queued after the target
        event is found (or on timeout), so they are not lost.

        Args:
            event_name: Event method name (e.g., 'Page.loadEventFired')
            timeout: Max seconds to wait

        Returns:
            The event params dict

        P0-1: aborts immediately on a WebSocket disconnect. Instead of
        blocking the full `timeout` waiting for an event that can no
        longer arrive, the loop wakes every 0.5s (or sooner) to check
        the `_abort_wait` flag set by `_handle_disconnect`. When the
        flag is set, we raise WebSocketDisconnected so the caller can
        trigger recovery instead of timing out 30s later.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        stashed: list[dict[str, Any]] = []

        # Fast path: if the connection is already dead, do not even
        # enter the loop. This catches callers that arrive AFTER the
        # socket was torn down (the `_abort_wait` flag is sticky).
        if self._abort_wait.is_set() or self._state in (
            ConnectionState.DOWN,
            ConnectionState.CLOSED,
        ):
            raise self._disconnect_error(
                f"CDP connection is {self._state.value} or aborted"
            )

        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError(f"Timed out waiting for event {event_name}")
                # Wake periodically to re-check _abort_wait instead of
                # blocking the full `remaining`. 0.5s strikes a balance
                # between latency and CPU.
                chunk = min(remaining, 0.5)
                try:
                    event = await asyncio.wait_for(
                        self._events.get(), timeout=chunk
                    )
                    if event.get("method") == event_name:
                        return event.get("params", {})
                    # Buffer non-matching events for re-queue
                    stashed.append(event)
                except asyncio.TimeoutError:
                    # No event in the chunk window; loop back to
                    # re-check abort flag and deadline.
                    if self._abort_wait.is_set() or self._state in (
                        ConnectionState.DOWN,
                        ConnectionState.CLOSED,
                    ):
                        raise self._disconnect_error(
                            "CDP WebSocket closed during wait_for_event"
                        ) from None
        finally:
            # Always re-queue stashed events so they are not lost
            for ev in stashed:
                await self._events.put(ev)

    async def attach_to_page(self) -> str:
        """
        Find the first page target and attach to it.

        Returns:
            The sessionId for the attached target.
        """
        targets = await self.send("Target.getTargets")
        page_targets = [
            t for t in targets.get("targetInfos", []) if t["type"] == "page"
        ]
        if not page_targets:
            raise RuntimeError("No page target found. Is a tab open?")

        target_id = page_targets[0]["targetId"]
        # P0-1: cache the target_id so the reconnect loop in
        # Commit 3 can re-attach to the same tab if Chrome kept it
        # alive across the WebSocket drop.
        self._target_id = target_id
        logger.info(f"Attaching to page target: {target_id}")

        # Attach to the initial target
        result = await self.send(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
        )
        self._session_id = result["sessionId"]
        logger.info(f"Attached with sessionId: {self._session_id}")
        
        # Turn on auto-attach to handle cross-origin process swaps (healing)
        await self.send(
            "Target.setAutoAttach",
            {
                "autoAttach": True, 
                "waitForDebuggerOnStart": False, 
                "flatten": True
            },
        )
        logger.debug("Enabled Target.setAutoAttach for session resilience")
        
        return self._session_id

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    @property
    def is_disconnected(self) -> bool:
        """True when the FSM is in a terminal-ish state.

        DOWN means the circuit breaker is open (consecutive failures
        exhausted). CLOSED means an explicit close() ran. Both are
        recoverable, but neither will accept new `send` calls.
        """
        return self._state in (ConnectionState.DOWN, ConnectionState.CLOSED)

    @property
    def reconnecting(self) -> bool:
        """True while the auto-reconnect/reattach machinery is in flight.

        Lets the recovery coordinator (AgenticLoop) distinguish "the
        connection is trying to heal itself right now" from "the
        circuit is open / the orchestrator tore us down".
        """
        return self._reconnect_task is not None and not self._reconnect_task.done()

    @property
    def state(self) -> ConnectionState:
        """Expose the current FSM state (mostly for diagnostics)."""
        return self._state

    async def wait_disconnected(self, timeout: Optional[float] = None) -> None:
        """Block until the circuit opens, or the timeout elapses.

        Returns immediately if the circuit is already open. Useful
        for callers that want to give the auto-reconnect a chance
        to finish before falling back to BrowserSession.recover().
        """
        if self.is_disconnected:
            return
        try:
            if timeout is None:
                await self._disconnect_event.wait()
            else:
                await asyncio.wait_for(
                    self._disconnect_event.wait(), timeout=timeout
                )
        except asyncio.TimeoutError:
            return

    async def wait_for_reconnect(
        self,
        timeout: float,
        poll_interval: float = 0.1,
    ) -> ConnectionState:
        """Wait, bounded by ``timeout``, for the FSM to leave DEGRADED.

        The auto-reconnect task heals the socket (HEALTHY), exhausts
        its attempts (DOWN), or is called off by teardown (CLOSED) on
        its own; this method only observes it so the agent loop can
        decide whether a full `BrowserSession.recover()` respawn is
        needed (READY-AI-T-3).

        Returns the final observed state:
          * HEALTHY — reconnect + reattach succeeded; continue on the
            same session.
          * DOWN / CLOSED — terminal; a respawn is required.
          * DEGRADED — the timeout elapsed while the reconnect was
            still in flight.

        Cancellation is not swallowed: `asyncio.CancelledError`
        propagates to the caller so an aborted run tears down
        cleanly. State comparisons are by value (==) because tests
        reload the state module mid-session.
        """
        if self._state != ConnectionState.DEGRADED:
            return self._state
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self._state == ConnectionState.DEGRADED:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll_interval, remaining))
        return self._state

    async def close(self) -> None:
        """Close the WebSocket connection and cancel background tasks.

        P0-1: marks the close as intentional so the recv loop does
        NOT schedule a reconnect when it sees ConnectionClosed.
        Cancels any in-flight reconnect task as well — important so
        that a teardown during a long backoff doesn't leave us
        trying to dial Chrome after the orchestrator already gave up.
        """
        # Mark the close as intentional BEFORE actually closing the
        # socket — the recv loop reads this flag in its except clause.
        self._intentional_close = True

        # Cancel any pending reconnect so the backoff sleep doesn't
        # outlive the close.
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except (asyncio.CancelledError, Exception):
                pass
        self._reconnect_task = None

        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None

        # Persist the final state for is_disconnected / wait_disconnected.
        async with self._state_lock:
            if self._state != ConnectionState.CLOSED:
                self._state = ConnectionState.CLOSED

        logger.info("CDP connection closed")
