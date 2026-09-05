"""
CDP Page Domain operations.

navigate, screenshot, DOM extraction, and wait-for-selector.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from ..observability import get_metrics
from .connection import CDPConnection, CDPEventContext
from .sanitize import (
    is_raw_mode,
    resolve_value_max,
    sanitize_html,
)

logger = logging.getLogger(__name__)

# Quick win #7 from the CDP resilience roadmap: cap the DOM HTML that
# the agent will hand to the LLM. SaaS pages (Stripe, Linear, Notion)
# routinely ship well over 100KB of HTML; the default 4 000-char
# cap that ships today is fine for small sites but starves the
# planner on the dense ones. 8 000 is a conservative middle ground.
ENV_DOM_MAX_CHARS = "READY_AI_DOM_MAX_CHARS"
DOM_MAX_CHARS_DEFAULT = 8_000
DOM_MAX_CHARS_LEGACY_DEFAULT = 4_000  # preserved as the no-env fallback


@dataclass(frozen=True)
class PassiveEvidence:
    """Small, sanitized proof from a CDP event (never a response body)."""

    kind: str
    passed: bool
    observed: str
    details: dict[str, Any]


def _sanitize_evidence_url(value: str) -> str:
    """Keep origin/path only; query strings often contain credentials."""
    try:
        parsed = urlsplit(value)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except (TypeError, ValueError):
        return ""


# Universal active cursor + highlight border injected on every new document.
# Registered via Page.addScriptToEvaluateOnNewDocument — must be re-registered
# whenever the CDP session is swapped (e.g. cross-origin process swap).
CURSOR_SCRIPT = """
    (() => {
        if (window.__browserAutoCursorMove) return;

        let cursor = null;
        let border = null;

        const initAssets = () => {
            if (document.getElementById('ready-ai-cursor-global')) return;

            border = document.createElement('div');
            border.id = 'ready-ai-border-global';
            border.style.position = 'fixed';
            border.style.inset = '0';
            border.style.pointerEvents = 'none';
            border.style.zIndex = '2147483646';
            border.style.boxShadow = 'inset 0 0 0 2px rgba(255, 215, 0, 0.4), inset 0 0 0 4px rgba(0, 0, 0, 0.5)';
            border.style.animation = 'ready-ai-pixel-smoke 1.2s steps(4, end) infinite alternate';

            const style = document.createElement('style');
            style.textContent = `
                @keyframes ready-ai-pixel-smoke {
                    0%   { box-shadow: inset 0 0 0 2px rgba(255, 215, 0, 0.4), inset 0 0 0 6px rgba(0, 0, 0, 0.4),  inset 0 0 4px 6px rgba(255, 215, 0, 0.2); }
                    33%  { box-shadow: inset 0 0 0 4px rgba(255, 215, 0, 0.6), inset 0 0 0 8px rgba(0, 0, 0, 0.6),  inset 0 0 6px 8px rgba(255, 215, 0, 0.3); }
                    66%  { box-shadow: inset 0 0 0 6px rgba(255, 215, 0, 0.5), inset 0 0 0 10px rgba(0, 0, 0, 0.7), inset 0 0 8px 10px rgba(255, 215, 0, 0.4); }
                    100% { box-shadow: inset 0 0 0 8px rgba(255, 215, 0, 0.8), inset 0 0 0 12px rgba(0, 0, 0, 0.9), inset 0 0 10px 14px rgba(255, 215, 0, 0.5); }
                }
                @keyframes ready-ai-ripple {
                    0% { transform: translate(-50%, -50%) scale(0.5); opacity: 1; }
                    100% { transform: translate(-50%, -50%) scale(3); opacity: 0; }
                }
            `;
            document.head.appendChild(style);

            cursor = document.createElement('div');
            cursor.id = 'ready-ai-cursor-global';
            cursor.style.position = 'fixed';
            cursor.style.width = '24px';
            cursor.style.height = '24px';
            cursor.style.pointerEvents = 'none';
            cursor.style.zIndex = '2147483647';
            cursor.style.transform = 'translate(-2px, -2px)';
            cursor.style.transition = 'left 0.3s ease-out, top 0.3s ease-out';
            cursor.innerHTML = `
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" style="filter: drop-shadow(0px 2px 4px rgba(0,0,0,0.5));">
                    <path d="M4 2L20 10L13 13L10 20L4 2Z" fill="#000000" stroke="#FFD700" stroke-width="2" stroke-linejoin="round"/>
                    <circle cx="11.5" cy="11.5" r="2" fill="#FFD700"/>
                </svg>
            `;
            cursor.style.left = '50%';
            cursor.style.top = '50%';

            document.documentElement.appendChild(border);
            document.documentElement.appendChild(cursor);
        };

        if (document.body || document.documentElement) {
            initAssets();
        } else {
            document.addEventListener('DOMContentLoaded', initAssets);
        }

        window.__browserAutoCursorMove = (x, y) => {
            if (!cursor && document.documentElement) initAssets();
            if (cursor) {
                cursor.style.left = x + 'px';
                cursor.style.top = y + 'px';
            }
        };

        window.__browserAutoCursorClickEffect = () => {
            if (!cursor) return;
            const ripple = document.createElement('div');
            ripple.style.position = 'fixed';
            ripple.style.left = cursor.style.left;
            ripple.style.top = cursor.style.top;
            ripple.style.width = '20px';
            ripple.style.height = '20px';
            ripple.style.borderRadius = '50%';
            ripple.style.backgroundColor = 'rgba(255, 215, 0, 0.5)';
            ripple.style.border = '2px solid rgba(255, 215, 0, 0.8)';
            ripple.style.pointerEvents = 'none';
            ripple.style.zIndex = '2147483646';
            ripple.style.transform = 'translate(-50%, -50%)';
            ripple.style.animation = 'ready-ai-ripple 0.4s ease-out forwards';
            document.documentElement.appendChild(ripple);
            setTimeout(() => ripple.remove(), 400);
        };
    })();
"""


async def register_cursor_script(conn, session_id=None) -> None:
    """
    (Re-)register the universal cursor/highlight script on a CDP session.
    Called from PageDomain.enable() and from connection auto-attach healing.
    """
    try:
        await conn.send(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": CURSOR_SCRIPT},
            session_id=session_id,
            timeout=5.0,
        )
    except Exception as exc:
        logger.debug(f"Cursor script registration failed: {exc}")


class PageDomain:
    """High-level Page domain operations over a CDPConnection."""

    def __init__(
        self,
        conn: CDPConnection,
        context: Optional[CDPEventContext] = None,
    ):
        self._conn = conn
        # A real BrowserSession has attached a CDP session before it creates a
        # PageDomain. Unit tests and older integrations without one keep the
        # legacy queue path until they opt in explicitly.
        self.context = context or (
            CDPEventContext(session_id=conn.session_id)
            if conn.session_id
            else None
        )
        # Quick win #6: short-lived cache for wait_for_network_idle so
        # back-to-back calls within the TTL don't repeat the event-loop
        # scan. Bounded by `time.monotonic()`; the value is a sentinel
        # `(monotonic_ts, idle_time)` tuple.
        self._network_idle_cache: tuple[float, float] | None = None
        self._network_idle_cache_ttl_s: float = 1.0
        self._network_enabled: bool = False

    @property
    def event_cursor(self) -> int:
        """Cursor captured before an action for post-action evidence."""
        return self._conn.event_cursor

    async def resolve_target_session(self, ref) -> str:
        """Resolve a flow-level tab reference to its CDP session id.

        Refreshes the registry from the browser first so out-of-process
        frames resolve even when their attach event hasn't arrived.
        Raises RuntimeError naming the known targets when unresolvable
        (fail-closed at dispatch time, never silently primary).
        """
        await self._conn.ensure_targets()
        try:
            return self._conn.targets.resolve(ref).session_id
        except (KeyError, AttributeError) as exc:
            raise RuntimeError(str(exc)) from exc

    def _is_scoped(self) -> bool:
        return self.context is not None and not self.context.is_empty

    async def _wait_event(
        self,
        event_name: str,
        timeout: float,
        *,
        after_sequence: Optional[int] = None,
    ) -> dict[str, Any]:
        return await self._conn.wait_for_event(
            event_name,
            timeout,
            context=self.context if self._is_scoped() else None,
            after_sequence=after_sequence,
        )

    async def enable(self) -> None:
        """Enable Page domain events (required for loadEventFired etc.) and universal cursor."""
        await self._conn.send("Page.enable")
        await self._conn.send("DOM.enable")
        # Passive observation only.  We deliberately do not enable Fetch or
        # request interception: no request is paused and no response body is
        # ever read by this task.
        await self._conn.send("Network.enable")
        self._network_enabled = True
        # Quick win #8: ask Chrome to emit Page.lifecycleEvent so we can
        # wait for networkIdle via a real event instead of a polling
        # window in wait_for_network_idle. Lifecycle events are cheap
        # (no payload) and let us drop the idle_time race.
        try:
            await self._conn.send("Page.setLifecycleEventsEnabled", {"enabled": True})
        except Exception as exc:
            logger.debug(f"setLifecycleEventsEnabled failed: {exc}")
        await register_cursor_script(self._conn)

    # ─── Multi-tab operations (READY-AI-T-PH2B) ──────────────────────
    #
    # Every method resolves through the connection's TargetRegistry and
    # names the context in its errors. Switching is the only path that
    # changes the primary session — automatic attaches never do.

    async def list_tabs(self) -> list[dict]:
        """Return attached page targets with registry session mapping."""
        infos = []
        for info in self._conn.targets.all():
            if info.type == "page":
                infos.append(
                    {
                        "target_id": info.target_id,
                        "session_id": info.session_id,
                        "url": info.url,
                    }
                )
        return infos

    async def wait_for_popup(
        self, timeout: float = 10.0, known_ids: "set[str] | None" = None
    ) -> dict:
        """Wait for a new page target and attach to it (registers session)."""
        known = set(known_ids) if known_ids is not None else {
            t.target_id for t in self._conn.targets.all()
        }
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            for target in await self._conn.list_targets():
                tid = str(target.get("targetId") or "")
                if tid and tid not in known:
                    # The target exists but its URL may not have committed
                    # yet (window.open lands on about:blank first). Wait
                    # best-effort for a real URL within the same budget so
                    # url-based resolution works right after the wait.
                    url = str(target.get("url") or "")
                    while not url or url == "about:blank":
                        if asyncio.get_running_loop().time() >= deadline:
                            break
                        await asyncio.sleep(0.25)
                        refreshed = {
                            str(t.get("targetId") or ""): str(t.get("url") or "")
                            for t in await self._conn.list_targets()
                        }
                        url = refreshed.get(tid, url)
                    existing = self._conn.targets.session_for_target(tid)
                    if existing is not None:
                        # Auto-attach won the race — reuse its session.
                        return {"target_id": tid, "session_id": existing}
                    try:
                        session_id = await self._conn.attach_to_target(tid)
                    except RuntimeError:
                        # Auto-attach won the race between getTargets and
                        # our attach; re-read the registry instead of failing.
                        existing = self._conn.targets.session_for_target(tid)
                        if existing is None:
                            raise
                        return {"target_id": tid, "session_id": existing}
                    self._conn.targets.register(
                        tid,
                        session_id,
                        type=str(target.get("type") or "page"),
                        url=url,
                    )
                    return {"target_id": tid, "session_id": session_id}
            await asyncio.sleep(0.25)
        raise TimeoutError(f"Timed out waiting for popup after {timeout:g}s")

    async def switch_to_tab(self, ref) -> dict:
        """Make a registered target the primary session (explicit switch)."""
        try:
            info = self._conn.targets.resolve(ref)
        except KeyError as exc:
            raise RuntimeError(str(exc)) from exc
        self._conn.switch_session(info.session_id)
        try:
            await self._conn.send("Target.activateTarget", {"targetId": info.target_id})
        except RuntimeError as exc:
            # The session switch already happened (the part that matters
            # headless); activation is best-effort window management.
            logger.debug(f"Target.activateTarget failed (ignored): {exc}")
        return {"target_id": info.target_id, "url": info.url}

    async def close_tab(self, ref=None) -> dict:
        """Close a target (default: primary) and fall back the session."""
        if ref is None:
            primary = self._conn.targets.primary_target_id
            if primary is None:
                raise RuntimeError("no tabs registered")
            info = self._conn.targets.resolve(primary)
        else:
            try:
                info = self._conn.targets.resolve(ref)
            except KeyError as exc:
                raise RuntimeError(str(exc)) from exc
        await self._conn.close_target(info.target_id)
        remaining = self._conn.targets.primary_target_id
        if remaining is not None:
            session = self._conn.targets.session_for_target(remaining)
            if session is not None:
                self._conn.switch_session(session)
        return {"closed": info.target_id, "active": remaining}

    async def navigate(self, url: str, wait_for_load: bool = True, wait_for_network: bool = True) -> None:
        """
        Navigate to a URL and optionally wait for page load and network idle.

        Args:
            url: Target URL
            wait_for_load: Whether to wait for Page.loadEventFired
            wait_for_network: Whether to wait for network idle (useful for SPAs)
        """
        # Validate URL scheme - only allow http and https schemes
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            raise ValueError(f"Invalid URL scheme: {parsed.scheme}. Only http and https schemes are allowed.")
        
        event_cursor = self.event_cursor
        logger.info(f"Navigating to: {url}")
        await self._conn.send("Page.navigate", {"url": url})
        
        if wait_for_load:
            try:
                await self._wait_event(
                    "Page.loadEventFired", timeout=30.0, after_sequence=event_cursor
                )
            except TimeoutError:
                logger.warning("Page load event timed out, continuing anyway")
                
        if wait_for_network:
            await self.wait_for_network_idle(
                timeout=10.0, idle_time=0.5, after_sequence=event_cursor
            )
        else:
            # Check for generic lifecycle events or basic readiness
            try:
                await self._wait_event(
                    "Page.domContentEventFired", timeout=2.0, after_sequence=event_cursor
                )
            except TimeoutError:
                pass
            
        logger.info("Navigation complete")

    async def wait_for_navigation_settled(
        self,
        timeout: float = 10.0,
        *,
        after_sequence: Optional[int] = None,
    ) -> bool:
        """
        Detect whether a navigation is in flight after an action and, if so,
        wait until the new document has loaded and the network has settled.

        Key properties:
          * Navigation marker events (frameStartedLoading/frameNavigated/
            targetCrashed/attachedToTarget) that trigger this barrier are
            CONSUMED — they are not re-queued, so a subsequent call cannot
            observe a stale navigation from a previous action.
          * Every blocking phase (load wait, domContent fallback, readyState
            poll, network idle) re-computes the remaining budget and bails
            out if time has expired, so the method never exceeds `timeout`.
        """
        nav_methods = {
            "Page.frameStartedLoading",
            "Page.frameRequestedNavigation",
            "Page.frameNavigated",
            "Inspector.targetCrashed",
            "Target.attachedToTarget",
        }
        # A scoped PageDomain never reads the compatibility queue.  Every
        # waiter receives its own event stream, and the action cursor lets us
        # inspect events which arrived just before this method was entered.
        if self._is_scoped():
            cursor = self.event_cursor if after_sequence is None else after_sequence
            subscription = self._conn.subscribe_events(
                context=self.context,
                after_sequence=cursor,
            )
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            try:
                navigated = False
                while loop.time() < deadline:
                    try:
                        event = await subscription.wait(max(deadline - loop.time(), 0.01))
                    except TimeoutError:
                        break
                    if event.get("method") in nav_methods:
                        navigated = True
                        break
                if not navigated:
                    return False
                remaining = max(deadline - loop.time(), 0.0)
                if remaining:
                    try:
                        await self._wait_event(
                            "Page.loadEventFired",
                            remaining / 2,
                            after_sequence=cursor,
                        )
                    except TimeoutError:
                        try:
                            await self._wait_event(
                                "Page.domContentEventFired",
                                max(deadline - loop.time(), 0.01) / 2,
                                after_sequence=cursor,
                            )
                        except TimeoutError:
                            pass
                remaining = max(deadline - loop.time(), 0.0)
                if remaining:
                    await self.wait_for_network_idle(
                        timeout=remaining,
                        idle_time=min(0.3, remaining / 2),
                        after_sequence=cursor,
                    )
                return True
            finally:
                subscription.close()

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        peek_deadline = loop.time() + 0.2
        stashed_non_nav: list[dict] = []  # non-nav events to re-queue
        navigated = False

        def remaining() -> float:
            return max(deadline - loop.time(), 0.0)

        try:
            # Phase 1: peek for navigation signals. Non-nav events get
            # re-queued. Nav events are CONSUMED (dropped) — finding one
            # flips `navigated` and we continue draining additional nav
            # markers so the queue is clean before phase 2.
            while True:
                peek_remaining = peek_deadline - loop.time()
                if peek_remaining <= 0:
                    break
                try:
                    event = await asyncio.wait_for(
                        self._conn._events.get(), timeout=peek_remaining
                    )
                except asyncio.TimeoutError:
                    break
                if event.get("method") in nav_methods:
                    navigated = True
                    # extend peek window briefly so we drain clustered markers
                    peek_deadline = max(peek_deadline, loop.time() + 0.05)
                    continue
                stashed_non_nav.append(event)

            if not navigated:
                return False

            # Phase 2a: Page.loadEventFired — half the remaining budget
            load_budget = remaining() / 2
            if load_budget > 0:
                try:
                    await self._wait_event(
                        "Page.loadEventFired", timeout=load_budget
                    )
                except TimeoutError:
                    logger.debug("loadEventFired missed; trying domContentEventFired")
                    # Phase 2b: domContentEventFired — up to half of what's left
                    dc_budget = remaining() / 2
                    if dc_budget > 0:
                        try:
                            await self._wait_event(
                                "Page.domContentEventFired", timeout=dc_budget
                            )
                        except TimeoutError:
                            logger.debug(
                                "domContentEventFired missed; polling readyState"
                            )
                            # Phase 2c: readyState poll — capped at remaining budget
                            poll_budget = min(2.0, remaining())
                            poll_deadline = loop.time() + poll_budget
                            while loop.time() < poll_deadline:
                                try:
                                    res = await self._conn.send(
                                        "Runtime.evaluate",
                                        {"expression": "document.readyState"},
                                        timeout=min(2.0, max(remaining(), 0.1)),
                                    )
                                    if res.get("result", {}).get("value") in (
                                        "interactive",
                                        "complete",
                                    ):
                                        break
                                except Exception:
                                    pass
                                await asyncio.sleep(min(0.2, remaining()))

            # Phase 3: settle network within whatever's left
            net_budget = min(5.0, remaining())
            if net_budget > 0:
                try:
                    await self.wait_for_network_idle(
                        timeout=net_budget, idle_time=min(0.3, net_budget / 2)
                    )
                except Exception:
                    pass

            return True
        finally:
            # Only re-queue non-nav events; consumed nav events stay dropped.
            for ev in stashed_non_nav:
                await self._conn._events.put(ev)

    async def wait_for_network_idle(
        self,
        timeout: float = 30.0,
        idle_time: float = 0.5,
        *,
        after_sequence: Optional[int] = None,
    ) -> None:
        """
        Wait until there are no pending network requests for at least `idle_time` seconds.

        Args:
            timeout: Maximum time to wait overall.
            idle_time: How long the network must be completely quiet to be considered idle.

        Quick win #6: results are memoized for `self._network_idle_cache_ttl_s`
        seconds on the same PageDomain instance. The orchestrator sometimes
        asks "is the network idle?" twice in a row (e.g. before screenshot
        and before DOM extraction); without the cache we'd pay the event
        loop scan twice. Bounded TTL so we never report stale idle state
        for a page that has gone active in the meantime.

        Quick win #8: when `READY_AI_USE_LIFECYCLE_EVENTS=true` is set, we
        prefer a single `Page.lifecycleEvent` (`name=networkIdle`) over
        the polling window. This avoids the busy-wait on sites that are
        fully idle for longer than `idle_time`. Falls back to the legacy
        polling behaviour when the flag is off or the event never comes.
        """
        now = asyncio.get_running_loop().time()
        if (
            after_sequence is None
            and
            self._network_idle_cache is not None
            and (now - self._network_idle_cache[0]) < self._network_idle_cache_ttl_s
        ):
            logger.debug("wait_for_network_idle: cache hit")
            return

        if self._is_scoped():
            await self._wait_for_scoped_network_idle(
                timeout=timeout,
                idle_time=idle_time,
                after_sequence=after_sequence,
            )
            return

        if os.environ.get("READY_AI_USE_LIFECYCLE_EVENTS", "").lower() in ("1", "true", "yes"):
            # Pull directly from the shared event queue so we can inspect
            # both the method *and* params.name. wait_for_event matches on
            # method only, so it would accept ANY Page.lifecycleEvent (load,
            # DOMContentLoaded, firstPaint …) as "network idle". We loop
            # until we see name == "networkIdle", re-queuing every other
            # event so concurrent waiters are not starved.
            deadline = asyncio.get_running_loop().time() + timeout
            stashed: list[dict] = []
            found_idle = False
            try:
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    chunk = min(remaining, 0.5)
                    try:
                        event = await asyncio.wait_for(
                            self._conn._events.get(), timeout=chunk
                        )
                        if (
                            event.get("method") == "Page.lifecycleEvent"
                            and event.get("params", {}).get("name") == "networkIdle"
                        ):
                            found_idle = True
                            break
                        # Not the lifecycle event we want — stash for re-queue.
                        stashed.append(event)
                    except asyncio.TimeoutError:
                        # No event in the chunk window; re-check deadline.
                        continue
            finally:
                # Re-queue non-matching events so other waiters see them.
                for ev in stashed:
                    await self._conn._events.put(ev)

            if found_idle:
                logger.debug("wait_for_network_idle: networkIdle lifecycle event received")
                self._network_idle_cache = (
                    asyncio.get_running_loop().time(),
                    idle_time,
                )
                return
            logger.debug("networkIdle lifecycle event not received, falling back to polling")
            # Fall through to the legacy polling implementation.

        # Preserve the legacy polling contract: callers that miss the short
        # cache refresh Network.enable on each scan. Scoped waits below keep a
        # single passive domain subscription instead.
        await self._conn.send("Network.enable")
        self._network_enabled = True
        deadline = asyncio.get_running_loop().time() + timeout
        in_flight = set()
        stashed = []
        idle_detected = False

        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    logger.warning("Network idle wait timed out (some requests may still be pending)")
                    break

                try:
                    # Wait for next event up to `idle_time`
                    event = await asyncio.wait_for(self._conn._events.get(), timeout=idle_time)
                    method = event.get("method", "")

                    if method == "Network.requestWillBeSent":
                        req_id = event.get("params", {}).get("requestId")
                        in_flight.add(req_id)
                    elif method in ("Network.loadingFinished", "Network.loadingFailed"):
                        req_id = event.get("params", {}).get("requestId")
                        in_flight.discard(req_id)
                    else:
                        # Not a network event we care about for this loop
                        stashed.append(event)

                except asyncio.TimeoutError:
                    # Timeout means no event was received for `idle_time` seconds
                    if not in_flight:
                        logger.debug("Network is idle")
                        idle_detected = True
                        break
        finally:
            # Re-queue non-network events so other waiters aren't starved
            for ev in stashed:
                await self._conn._events.put(ev)
            # Only cache when the network was actually detected as idle.
            # Caching on timeout would cause subsequent calls within the
            # TTL to return a stale 'idle' result even though requests
            # may still be in flight.
            if idle_detected:
                self._network_idle_cache = (
                    asyncio.get_running_loop().time(),
                    idle_time,
                )

    async def _wait_for_scoped_network_idle(
        self,
        *,
        timeout: float,
        idle_time: float,
        after_sequence: Optional[int],
    ) -> None:
        """Network-idle wait backed by a private context subscription.

        This path intentionally does not requeue anything: the subscription
        owns a copy, while navigation and expectation waits receive their own
        copies from the connection router.
        """
        if not self._network_enabled:
            await self._conn.send("Network.enable")
            self._network_enabled = True
        cursor = self.event_cursor if after_sequence is None else after_sequence
        subscription = self._conn.subscribe_events(
            context=self.context,
            after_sequence=cursor,
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        in_flight: set[str] = set()
        idle_detected = False
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    logger.warning("Scoped network idle wait timed out")
                    return
                try:
                    event = await subscription.wait(min(idle_time, remaining))
                except TimeoutError:
                    if not in_flight:
                        idle_detected = True
                        return
                    continue
                method = event.get("method", "")
                params = event.get("params") or {}
                request_id = params.get("requestId")
                if method == "Network.requestWillBeSent" and request_id:
                    in_flight.add(str(request_id))
                elif method in ("Network.loadingFinished", "Network.loadingFailed") and request_id:
                    in_flight.discard(str(request_id))
        finally:
            subscription.close()
            if idle_detected:
                self._network_idle_cache = (loop.time(), idle_time)

    async def _evaluate_value(self, expression: str) -> Any:
        """Evaluate a small assertion expression without exposing DOM output."""
        result = await self._conn.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=5.0,
        )
        remote = result.get("result", {})
        return remote.get("value")

    async def wait_for_element(
        self,
        selector: str,
        *,
        state: str = "visible",
        timeout: float = 10.0,
        stable_for: float = 0.2,
    ) -> bool:
        """Deterministically wait for an element to be visible/enabled/stable.

        ``stable`` means its visible bounding rectangle stayed unchanged for
        ``stable_for`` seconds. It deliberately avoids a screenshot or DOM
        dump, keeping evidence compact and safe.
        """
        if state not in {"present", "visible", "enabled", "stable"}:
            raise ValueError("state must be present, visible, enabled, or stable")
        safe_selector = json.dumps(selector)
        expression = f"""(() => {{
            const el = document.querySelector({safe_selector});
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return {{
              visible: !!(rect.width && rect.height) && style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) > 0,
              enabled: !el.disabled && el.getAttribute('aria-disabled') !== 'true',
              rect: [Math.round(rect.x), Math.round(rect.y), Math.round(rect.width), Math.round(rect.height)]
            }};
        }})()"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        stable_since: Optional[float] = None
        last_rect: Optional[list[Any]] = None
        while loop.time() < deadline:
            result = await self._evaluate_value(expression)
            if not result:
                stable_since, last_rect = None, None
            elif state == "present":
                return True
            elif state == "visible" and result.get("visible"):
                return True
            elif state == "enabled" and result.get("visible") and result.get("enabled"):
                return True
            elif state == "stable" and result.get("visible"):
                rect = result.get("rect")
                if rect == last_rect:
                    stable_since = stable_since or loop.time()
                    if loop.time() - stable_since >= stable_for:
                        return True
                else:
                    last_rect, stable_since = rect, loop.time()
            await asyncio.sleep(min(0.1, max(deadline - loop.time(), 0.01)))
        return False

    async def wait_for_url(
        self,
        expected: str,
        *,
        mode: str = "contains",
        timeout: float = 10.0,
    ) -> bool:
        """Wait for a URL exact match or safe substring match."""
        if mode not in {"exact", "contains"}:
            raise ValueError("mode must be exact or contains")
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            current = str(await self._evaluate_value("window.location.href") or "")
            if (mode == "exact" and current == expected) or (mode == "contains" and expected in current):
                return True
            await asyncio.sleep(0.1)
        return False

    async def wait_for_text(
        self,
        text: str,
        *,
        selector: Optional[str] = None,
        timeout: float = 10.0,
    ) -> bool:
        """Wait for visible text, optionally constrained to one element."""
        root = (
            f"document.querySelector({json.dumps(selector)})"
            if selector
            else "document.body"
        )
        expression = f"({root}?.innerText || '').includes({json.dumps(text)})"
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if await self._evaluate_value(expression):
                return True
            await asyncio.sleep(0.1)
        return False

    async def wait_for_ax_text(self, text: str, *, timeout: float = 10.0) -> bool:
        """Wait for sanitized accessibility-tree evidence to contain text."""
        from .accessibility import get_ax_snapshot

        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if text in await get_ax_snapshot(self._conn):
                return True
            await asyncio.sleep(0.15)
        return False

    @staticmethod
    def _response_evidence(event: dict[str, Any]) -> PassiveEvidence:
        response = ((event.get("params") or {}).get("response") or {})
        status = int(response.get("status") or 0)
        url = _sanitize_evidence_url(str(response.get("url") or ""))
        return PassiveEvidence(
            kind="network_http",
            passed=200 <= status < 400,
            observed=f"HTTP {status}" if status else "HTTP status unavailable",
            details={"status": status, "url": url},
        )

    def http_failures_since(self, sequence: int) -> list[PassiveEvidence]:
        """Return passive 4xx/5xx outcomes for this page context only."""
        events = self._conn.events_since(
            sequence,
            context=self.context if self._is_scoped() else None,
            event_name="Network.responseReceived",
        )
        evidence = [self._response_evidence(event) for event in events]
        return [item for item in evidence if item.details.get("status", 0) >= 400]

    async def wait_for_http(
        self,
        *,
        status: Optional[int] = None,
        url_contains: Optional[str] = None,
        timeout: float = 10.0,
        after_sequence: Optional[int] = None,
    ) -> Optional[PassiveEvidence]:
        """Wait for a passive Network.responseReceived observation.

        Only the status and a query-less URL are retained. Fetch interception
        and response bodies are intentionally out of scope.
        """
        cursor = self.event_cursor if after_sequence is None else after_sequence
        subscription = self._conn.subscribe_events(
            context=self.context if self._is_scoped() else None,
            event_name="Network.responseReceived",
            after_sequence=cursor,
        )
        deadline = asyncio.get_running_loop().time() + timeout
        try:
            while asyncio.get_running_loop().time() < deadline:
                try:
                    event = await subscription.wait(max(deadline - asyncio.get_running_loop().time(), 0.01))
                except TimeoutError:
                    return None
                evidence = self._response_evidence(event)
                if status is not None and evidence.details["status"] != status:
                    continue
                if url_contains and url_contains not in evidence.details["url"]:
                    continue
                return evidence
            return None
        finally:
            subscription.close()

    async def wait_for_download(
        self,
        *,
        filename: Optional[str] = None,
        timeout: float = 10.0,
        after_sequence: Optional[int] = None,
    ) -> Optional[PassiveEvidence]:
        """Wait for the download-start event without reading file contents."""
        cursor = self.event_cursor if after_sequence is None else after_sequence
        subscription = self._conn.subscribe_events(
            context=self.context if self._is_scoped() else None,
            event_name="Page.downloadWillBegin",
            after_sequence=cursor,
        )
        deadline = asyncio.get_running_loop().time() + timeout
        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return None
                try:
                    event = await subscription.wait(remaining)
                except TimeoutError:
                    return None
                params = event.get("params") or {}
                suggested = str(params.get("suggestedFilename") or "")
                if filename and suggested != filename:
                    continue
                return PassiveEvidence(
                    kind="download",
                    passed=True,
                    observed="download started",
                    details={
                        "filename": suggested,
                        "url": _sanitize_evidence_url(str(params.get("url") or "")),
                    },
                )
        finally:
            subscription.close()


    async def screenshot(
        self,
        format: str = "png",
        quality: Optional[int] = None,
        full_page: bool = False,
    ) -> str:
        """
        Capture a screenshot of the current page.

        Args:
            format: Image format ('png' or 'jpeg')
            quality: JPEG quality (1-100), ignored for PNG
            full_page: Capture full scrollable page

        Returns:
            Base64-encoded image data
        """
        params: dict = {"format": format}
        if quality and format == "jpeg":
            params["quality"] = quality
        if full_page:
            # Get full page metrics
            metrics = await self._conn.send("Page.getLayoutMetrics")
            content_size = metrics.get("contentSize", metrics.get("cssContentSize", {}))
            params["clip"] = {
                "x": 0,
                "y": 0,
                "width": content_size.get("width", 1920),
                "height": content_size.get("height", 1080),
                "scale": 1,
            }

        result = await self._conn.send("Page.captureScreenshot", params)
        data = result.get("data", "")
        logger.debug(f"Screenshot captured: {len(data)} chars base64")
        return data

    async def get_dom_html(self, max_length: Optional[int] = None) -> str:
        """
        Get the outer HTML of the document.

        Args:
            max_length: Truncate the HTML to this many characters (for LLM
                context). When omitted, reads the `READY_AI_DOM_MAX_CHARS`
                env var and falls back to a conservative default
                (8 000 chars). The historical default of 4 000 is still
                honoured when the env var is explicitly set to 0 or empty
                in legacy deployments; new deployments get the larger cap.

        Returns:
            HTML string
        """
        # Force JS properties (value, checked) to be reflected into HTML attributes
        # so that React state changes are visible in the outerHTML snapshot.
        await self._conn.send("Runtime.evaluate", {
            "expression": """(() => {
                document.querySelectorAll('input, select, textarea').forEach(el => {
                    if (el.type === 'checkbox' || el.type === 'radio') {
                        if (el.checked) el.setAttribute('checked', 'checked');
                        else el.removeAttribute('checked');
                    } else if (el.value !== undefined) {
                        el.setAttribute('value', el.value);
                    }
                });
            })()"""
        })

        doc_result = await self._conn.send("DOM.getDocument", {"depth": -1})
        root_node_id = doc_result["root"]["nodeId"]

        html_result = await self._conn.send(
            "DOM.getOuterHTML", {"nodeId": root_node_id}
        )
        html = html_result.get("outerHTML", "")

        # P1-2: sanitize the HTML before it goes to the LLM. Sensitive
        # values (passwords, credit-card numbers, PII) are always
        # redacted; non-sensitive long values are truncated. The
        # READY_AI_RAW_DOM env opt-out is honoured for debug/dev.
        sanitized = sanitize_html(
            html,
            raw=is_raw_mode(),
            value_max=resolve_value_max(),
        )
        html = sanitized.html
        self._record_sanitize_metrics(sanitized.counters, source="dom")

        if max_length is None:
            max_length = self._resolve_dom_max_chars()
        if max_length and len(html) > max_length:
            html = html[:max_length] + "\n<!-- ... truncated ... -->"

        logger.debug(f"DOM HTML: {len(html)} chars (cap={max_length})")
        return html

    @staticmethod
    def _record_sanitize_metrics(counters, *, source: str) -> None:
        """Emit per-pass counters from a sanitize_html() pass.

        Each counter is a zero-or-positive integer keyed by `source=`
        (dom, ax, interactive) so the metrics layer can split them
        later in dashboards.
        """
        metrics = get_metrics()
        if metrics is None:
            return
        for name, value in counters.to_dict().items():
            if value <= 0:
                continue
            metrics.increment(
                f"cdp.sanitize.{name}",
                value=value,
                source=source,
            )

    @staticmethod
    def _resolve_dom_max_chars() -> int:
        """
        Resolve the effective DOM cap from the env, logging once if the
        user-supplied value is invalid. The cap is intentionally a soft
        limit; the LLM is still served the full HTML up to this many
        characters, with a sentinel appended to make truncation obvious.
        """
        raw = os.environ.get(ENV_DOM_MAX_CHARS)
        if raw is None or raw.strip() == "":
            return DOM_MAX_CHARS_DEFAULT
        try:
            value = int(raw.strip())
        except ValueError:
            logger.warning(
                f"Invalid {ENV_DOM_MAX_CHARS}={raw!r}; expected integer. "
                f"Falling back to default {DOM_MAX_CHARS_DEFAULT}."
            )
            return DOM_MAX_CHARS_DEFAULT
        if value < 0:
            logger.warning(
                f"Negative {ENV_DOM_MAX_CHARS}={value}; treating as no cap."
            )
            return 0
        return value

    async def wait_for_selector(
        self, selector: str, timeout: float = 10.0
    ) -> bool:
        """
        Poll for an element matching a CSS selector.

        Args:
            selector: CSS selector
            timeout: Max wait time in seconds

        Returns:
            True if found, False if timed out
        """
        js = f"!!document.querySelector({json.dumps(selector)})"
        deadline = asyncio.get_running_loop().time() + timeout

        while asyncio.get_running_loop().time() < deadline:
            result = await self._conn.send(
                "Runtime.evaluate", {"expression": js}
            )
            value = result.get("result", {}).get("value")
            if value:
                return True
            await asyncio.sleep(0.5)

        logger.warning(f"Selector '{selector}' not found within {timeout}s")
        return False

    async def get_page_title(self) -> str:
        """Get the document title."""
        result = await self._conn.send(
            "Runtime.evaluate", {"expression": "document.title"}
        )
        return result.get("result", {}).get("value", "")
