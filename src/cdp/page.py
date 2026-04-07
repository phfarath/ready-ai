"""
CDP Page Domain operations.

navigate, screenshot, DOM extraction, and wait-for-selector.
"""

import asyncio
import json
import logging
from typing import Optional

from .connection import CDPConnection

logger = logging.getLogger(__name__)


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

    def __init__(self, conn: CDPConnection):
        self._conn = conn

    async def enable(self) -> None:
        """Enable Page domain events (required for loadEventFired etc.) and universal cursor."""
        await self._conn.send("Page.enable")
        await self._conn.send("DOM.enable")
        await register_cursor_script(self._conn)

    async def navigate(self, url: str, wait_for_load: bool = True, wait_for_network: bool = True) -> None:
        """
        Navigate to a URL and optionally wait for page load and network idle.

        Args:
            url: Target URL
            wait_for_load: Whether to wait for Page.loadEventFired
            wait_for_network: Whether to wait for network idle (useful for SPAs)
        """
        logger.info(f"Navigating to: {url}")
        await self._conn.send("Page.navigate", {"url": url})
        
        if wait_for_load:
            try:
                await self._conn.wait_for_event("Page.loadEventFired", timeout=30.0)
            except TimeoutError:
                logger.warning("Page load event timed out, continuing anyway")
                
        if wait_for_network:
            await self.wait_for_network_idle(timeout=10.0, idle_time=0.5)
        else:
            # Check for generic lifecycle events or basic readiness
            try:
                await self._conn.wait_for_event("Page.domContentEventFired", timeout=2.0)
            except TimeoutError:
                pass
            
        logger.info("Navigation complete")

    async def wait_for_navigation_settled(self, timeout: float = 10.0) -> bool:
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
        loop = asyncio.get_event_loop()
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
                    await self._conn.wait_for_event(
                        "Page.loadEventFired", timeout=load_budget
                    )
                except TimeoutError:
                    logger.debug("loadEventFired missed; trying domContentEventFired")
                    # Phase 2b: domContentEventFired — up to half of what's left
                    dc_budget = remaining() / 2
                    if dc_budget > 0:
                        try:
                            await self._conn.wait_for_event(
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

    async def wait_for_network_idle(self, timeout: float = 30.0, idle_time: float = 0.5) -> None:
        """
        Wait until there are no pending network requests for at least `idle_time` seconds.

        Args:
            timeout: Maximum time to wait overall.
            idle_time: How long the network must be completely quiet to be considered idle.
        """
        await self._conn.send("Network.enable")
        deadline = asyncio.get_event_loop().time() + timeout
        in_flight = set()
        stashed = []
        
        try:
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
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
                        break
        finally:
            # Re-queue non-network events so other waiters aren't starved
            for ev in stashed:
                await self._conn._events.put(ev)


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
            max_length: Truncate the HTML to this many characters (for LLM context)

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

        if max_length and len(html) > max_length:
            html = html[:max_length] + "\n<!-- ... truncated ... -->"

        logger.debug(f"DOM HTML: {len(html)} chars")
        return html

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
        deadline = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < deadline:
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
