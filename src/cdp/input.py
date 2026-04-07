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
        if (window.__browserAutoCursorMove) {{
            window.__browserAutoCursorMove({x}, {y});
        }}
        """
        await self._conn.send("Runtime.evaluate", {"expression": js})
        
    async def show_click_effect(self) -> None:
        """
        Animate the cursor to show a click effect.
        """
        js = """
        if (window.__browserAutoCursorClickEffect) {
            window.__browserAutoCursorClickEffect();
        }
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
        await asyncio.sleep(0.1)  # small delay for cursor animation

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
                await asyncio.sleep(0.1)  # small delay for cursor animation

            await asyncio.sleep(0.05)  # give focus time to settle

        logger.info(f"Typing {len(text)} chars")

        # React (and other reactive frameworks) bind their state to controlled
        # inputs via the native value setter — bare CDP keyDown/keyUp events do
        # NOT update React state, so the input appears to ignore the keystrokes.
        # Instead, locate the target element (selector or activeElement), call
        # the native value setter, and dispatch synthetic 'input' + 'change'
        # events. Mirrors src/agent/browser_session.py:223-253.
        safe_text = json.dumps(text)
        safe_selector = json.dumps(selector) if selector else "null"
        js = f"""
        (() => {{
            const sel = {safe_selector};
            const el = sel
                ? document.querySelector(sel)
                : document.activeElement;
            if (!el) return {{ok: false, reason: 'no element'}};

            const value = {safe_text};

            if (el.isContentEditable) {{
                el.focus();
                try {{
                    document.execCommand('insertText', false, value);
                }} catch (e) {{
                    el.textContent = (el.textContent || '') + value;
                }}
                el.dispatchEvent(new InputEvent('input', {{
                    bubbles: true, cancelable: true,
                    inputType: 'insertText', data: value
                }}));
                return {{ok: true}};
            }}

            const proto = el instanceof HTMLTextAreaElement
                ? HTMLTextAreaElement.prototype
                : HTMLInputElement.prototype;
            const nativeSetter = Object.getOwnPropertyDescriptor(proto, 'value').set;
            if (!nativeSetter) return {{ok: false, reason: 'no setter'}};

            el.focus();
            try {{ el.select(); }} catch (e) {{}}
            nativeSetter.call(el, value);
            el.dispatchEvent(new InputEvent('input', {{
                bubbles: true, cancelable: true,
                inputType: 'insertText', data: value
            }}));
            // NOTE: we intentionally do NOT dispatch 'change' here. In real
            // user input, change fires on commit (blur), not during typing.
            // Emitting it inline triggers premature validation/autosave and
            // can double-fire listeners that already react to 'input'. The
            // change event will fire naturally on the next blur (e.g. when
            // focus moves to the next field or the submit button is clicked).
            return {{ok: true}};
        }})()
        """
        result = await self._conn.send(
            "Runtime.evaluate",
            {"expression": js, "returnByValue": True},
        )
        value = result.get("result", {}).get("value") or {}
        if not value.get("ok"):
            raise RuntimeError(
                f"type_text failed: {value.get('reason', 'unknown')}"
            )

        # Small settle delay so downstream wait_for_network_idle has a tick.
        await asyncio.sleep(max(delay, 0.05))

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
        await asyncio.sleep(0.1)  # Let scroll settle briefly
