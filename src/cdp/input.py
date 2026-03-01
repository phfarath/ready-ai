"""
CDP Input Domain — human-like mouse and keyboard simulation.

Uses DOM.querySelector → DOM.getBoxModel → Input.dispatchMouseEvent for clicks,
and Input.dispatchKeyEvent for typing.
"""

import asyncio
import json
import logging
from typing import Optional

from .connection import CDPConnection

logger = logging.getLogger(__name__)


class InputDomain:
    """Simulate mouse clicks, keyboard input, and scrolling via CDP."""

    def __init__(self, conn: CDPConnection):
        self._conn = conn
        
    async def move_cursor(self, x: float, y: float) -> None:
        """
        Inject a visual red dot cursor and move it to (x, y).
        
        Args:
            x: Target X coordinate
            y: Target Y coordinate
        """
        js = f"""
        (() => {{
            let cursor = document.getElementById('browser-auto-cursor');
            if (!cursor) {{
                cursor = document.createElement('div');
                cursor.id = 'browser-auto-cursor';
                cursor.style.position = 'fixed';
                cursor.style.width = '20px';
                cursor.style.height = '20px';
                cursor.style.borderRadius = '50%';
                cursor.style.backgroundColor = 'rgba(255, 0, 0, 0.5)';
                cursor.style.border = '2px solid rgba(255, 0, 0, 0.8)';
                cursor.style.pointerEvents = 'none';
                cursor.style.zIndex = '2147483647'; // Max z-index
                cursor.style.transform = 'translate(-50%, -50%)';
                cursor.style.transition = 'left 0.3s ease-out, top 0.3s ease-out, width 0.1s, height 0.1s';
                document.body.appendChild(cursor);
            }}
            cursor.style.left = {x} + 'px';
            cursor.style.top = {y} + 'px';
        }})()
        """
        await self._conn.send("Runtime.evaluate", {"expression": js})
        
    async def show_click_effect(self) -> None:
        """
        Animate the cursor to show a click effect.
        """
        js = """
        (() => {
            let cursor = document.getElementById('browser-auto-cursor');
            if (cursor) {
                cursor.style.width = '10px';
                cursor.style.height = '10px';
                cursor.style.backgroundColor = 'rgba(255, 0, 0, 0.8)';
                setTimeout(() => {
                    cursor.style.width = '20px';
                    cursor.style.height = '20px';
                    cursor.style.backgroundColor = 'rgba(255, 0, 0, 0.5)';
                }, 150);
            }
        })()
        """
        await self._conn.send("Runtime.evaluate", {"expression": js})

    async def click(self, selector: str, delay: float = 0.1) -> bool:
        """
        Click an element by CSS selector.

        Resolves the element's bounding box via DOM.getBoxModel, computes
        the center point, and dispatches mousePressed + mouseReleased events.

        Args:
            selector: CSS selector for the target element
            delay: Seconds between press and release (simulates human click)

        Returns:
            True if clicked successfully, False if element not found
        """
        # Resolve the DOM node
        doc = await self._conn.send("DOM.getDocument")
        root_id = doc["root"]["nodeId"]

        try:
            result = await self._conn.send(
                "DOM.querySelector",
                {"nodeId": root_id, "selector": selector},
            )
        except RuntimeError:
            logger.warning(f"querySelector failed for: {selector}")
            return False

        node_id = result.get("nodeId", 0)
        if node_id == 0:
            logger.warning(f"Element not found: {selector}")
            return False

        # Get the element's box model
        try:
            box = await self._conn.send("DOM.getBoxModel", {"nodeId": node_id})
        except RuntimeError:
            logger.warning(f"getBoxModel failed for: {selector}")
            return False

        # Box model content quad: [x1,y1, x2,y2, x3,y3, x4,y4]
        quad = box["model"]["content"]
        center_x = (quad[0] + quad[2] + quad[4] + quad[6]) / 4
        center_y = (quad[1] + quad[3] + quad[5] + quad[7]) / 4

        logger.info(f"Clicking '{selector}' at ({center_x:.0f}, {center_y:.0f})")

        # Move the visual cursor to the click coordinates
        await self.move_cursor(center_x, center_y)
        await asyncio.sleep(0.3)

        # Dispatch mouse events
        await self._conn.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mousePressed",
                "x": center_x,
                "y": center_y,
                "button": "left",
                "clickCount": 1,
            },
        )

        await self.show_click_effect()
        
        await asyncio.sleep(delay)

        await self._conn.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseReleased",
                "x": center_x,
                "y": center_y,
                "button": "left",
                "clickCount": 1,
            },
        )

        return True

    async def type_text(
        self, text: str, delay: float = 0.05, selector: Optional[str] = None
    ) -> None:
        """
        Type text character by character using Input.dispatchKeyEvent.

        Args:
            text: Text to type
            delay: Seconds between keystrokes
            selector: Optional CSS selector to focus first
        """
        if selector:
            # Focus the element first
            await self._conn.send(
                "Runtime.evaluate",
                {"expression": f"""(() => {{
                    const el = document.querySelector({json.dumps(selector)});
                    if (el) {{
                        el.focus();
                        const rect = el.getBoundingClientRect();
                        const centerX = rect.left + rect.width / 2;
                        const centerY = rect.top + rect.height / 2;
                        return {{x: centerX, y: centerY}};
                    }}
                    return null;
                }})()""", "returnByValue": True},
            )
            
            # Fetch coordinates to move cursor
            res = await self._conn.send("Runtime.evaluate", {"expression": f"""
                (() => {{
                    const el = document.querySelector({json.dumps(selector)});
                    if (el) {{
                        const r = el.getBoundingClientRect();
                        return {{x: r.left + r.width/2, y: r.top + r.height/2}};
                    }}
                    return null;
                }})()
            """, "returnByValue": True})
            
            if res.get("result", {}).get("value"):
                coords = res["result"]["value"]
                await self.move_cursor(coords["x"], coords["y"])
                await asyncio.sleep(0.2)

            await asyncio.sleep(0.1)

        logger.info(f"Typing {len(text)} chars")

        for char in text:
            # Use insertText for reliable character input
            await self._conn.send(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyDown",
                    "key": char,
                    "text": char,
                },
            )
            await self._conn.send(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyUp",
                    "key": char,
                },
            )
            await asyncio.sleep(delay)

    async def press_key(self, key: str) -> None:
        """
        Dispatch a single key press (e.g., 'Enter', 'Tab', 'Escape').

        Args:
            key: Key name as defined in CDP Input domain
        """
        logger.debug(f"Pressing key: {key}")
        await self._conn.send(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "key": key},
        )
        await self._conn.send(
            "Input.dispatchKeyEvent",
            {"type": "keyUp", "key": key},
        )

    async def scroll(
        self,
        x: float = 0,
        y: float = 0,
        delta_x: float = 0,
        delta_y: float = -300,
    ) -> None:
        """
        Dispatch a mouse wheel scroll event.

        Args:
            x: Mouse X position
            y: Mouse Y position
            delta_x: Horizontal scroll amount
            delta_y: Vertical scroll amount (negative = scroll down)
        """
        logger.debug(f"Scrolling: dx={delta_x}, dy={delta_y}")
        await self._conn.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseWheel",
                "x": x,
                "y": y,
                "deltaX": delta_x,
                "deltaY": delta_y,
            },
        )
        await asyncio.sleep(0.3)  # Let scroll settle
