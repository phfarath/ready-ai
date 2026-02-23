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
        """Enable Page domain events (required for loadEventFired etc.)."""
        await self._conn.send("Page.enable")
        await self._conn.send("DOM.enable")

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
