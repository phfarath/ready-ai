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


class PageDomain:
    """High-level Page domain operations over a CDPConnection."""

    def __init__(self, conn: CDPConnection):
        self._conn = conn

    async def enable(self) -> None:
        """Enable Page domain events (required for loadEventFired etc.) and universal cursor."""
        await self._conn.send("Page.enable")
        await self._conn.send("DOM.enable")
        
        # Inject universal active cursor on every new document load
        cursor_script = """
            (() => {
                if (window.__browserAutoCursorMove) return;
                
                let cursor = null;
                let border = null;
                
                const initAssets = () => {
                    if (document.getElementById('browser-auto-cursor-global')) return;
                    
                    // 1. Create the Animated Active Border
                    border = document.createElement('div');
                    border.id = 'browser-auto-border-global';
                    border.style.position = 'fixed';
                    border.style.inset = '0';
                    border.style.pointerEvents = 'none';
                    border.style.zIndex = '2147483646'; // Max z-index - 1
                    
                    // Black and yellow pixelated glowing border
                    border.style.boxShadow = 'inset 0 0 0 6px rgba(255, 215, 0, 0.6), inset 0 0 0 10px rgba(0, 0, 0, 0.8)';
                    border.style.animation = 'browser-auto-pulse 0.8s steps(3, end) infinite alternate';
                    
                    // Add keyframes for border pulse and ripple effect
                    const style = document.createElement('style');
                    style.textContent = `
                        @keyframes browser-auto-pulse {
                            0% { box-shadow: inset 0 0 0 4px rgba(255, 215, 0, 0.4), inset 0 0 0 8px rgba(0, 0, 0, 0.6); }
                            100% { box-shadow: inset 0 0 0 10px rgba(255, 215, 0, 0.9), inset 0 0 0 14px rgba(0, 0, 0, 0.9); }
                        }
                        @keyframes browser-auto-ripple {
                            0% { transform: translate(-50%, -50%) scale(0.5); opacity: 1; }
                            100% { transform: translate(-50%, -50%) scale(3); opacity: 0; }
                        }
                    `;
                    document.head.appendChild(style);

                    // 2. Create the Custom SVG Cursor
                    cursor = document.createElement('div');
                    cursor.id = 'browser-auto-cursor-global';
                    cursor.style.position = 'fixed';
                    cursor.style.width = '24px';
                    cursor.style.height = '24px';
                    cursor.style.pointerEvents = 'none';
                    cursor.style.zIndex = '2147483647';
                    cursor.style.transform = 'translate(-2px, -2px)';
                    cursor.style.transition = 'left 0.3s ease-out, top 0.3s ease-out';
                    
                    // SVG embedded (Black body, yellow border/details)
                    cursor.innerHTML = `
                        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" style="filter: drop-shadow(0px 2px 4px rgba(0,0,0,0.5));">
                            <path d="M4 2L20 10L13 13L10 20L4 2Z" fill="#000000" stroke="#FFD700" stroke-width="2" stroke-linejoin="round"/>
                            <circle cx="11.5" cy="11.5" r="2" fill="#FFD700"/>
                        </svg>
                    `;
                    
                    // Default starting position
                    cursor.style.left = '50%';
                    cursor.style.top = '50%';
                    
                    document.documentElement.appendChild(border);
                    document.documentElement.appendChild(cursor);
                };
                
                // Initialize as soon as possible, or fallback to DOMContentLoaded
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
                    
                    // Create a ripple element at the current cursor position
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
                    ripple.style.zIndex = '2147483646'; // Below the cursor
                    ripple.style.transform = 'translate(-50%, -50%)';
                    ripple.style.animation = 'browser-auto-ripple 0.4s ease-out forwards';
                    
                    document.documentElement.appendChild(ripple);
                    
                    // Remove after animation completes
                    setTimeout(() => ripple.remove(), 400);
                };
            })();
        """
        await self._conn.send("Page.addScriptToEvaluateOnNewDocument", {"source": cursor_script})

    async def navigate(self, url: str, wait_for_load: bool = True) -> None:
        """
        Navigate to a URL and optionally wait for page load.

        Args:
            url: Target URL
            wait_for_load: Whether to wait for Page.loadEventFired
        """
        logger.info(f"Navigating to: {url}")
        await self._conn.send("Page.navigate", {"url": url})
        if wait_for_load:
            try:
                await self._conn.wait_for_event("Page.loadEventFired", timeout=30.0)
            except TimeoutError:
                logger.warning("Page load event timed out, continuing anyway")
            # Extra settle time for dynamic content
            await asyncio.sleep(1.5)
        logger.info("Navigation complete")

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
