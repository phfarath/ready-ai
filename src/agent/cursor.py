"""
Cursor Animator — visual cursor effects and element highlighting.

Provides a background "thinking" cursor animation and element
highlighting/clearing for screenshots.
"""

import asyncio
import json
import logging
import re
from typing import Optional

from ..cdp.connection import CDPConnection
from ..cdp.runtime import RuntimeDomain

logger = logging.getLogger(__name__)


class CursorAnimator:
    """Background cursor animation and element highlight management."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._conn: Optional[CDPConnection] = None
        self._moving = False

    @property
    def moving(self) -> bool:
        return self._moving

    @moving.setter
    def moving(self, value: bool) -> None:
        self._moving = value

    def start(self, conn: CDPConnection) -> None:
        """Spawn the background cursor animation task."""
        self._conn = conn
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Cancel the background task and wait for it to finish."""
        self._moving = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        """Background task that slightly moves the cursor simulating 'thinking'."""
        import random
        curr_x, curr_y = 500, 500
        while True:
            try:
                await asyncio.sleep(random.uniform(1.0, 3.0))
                if not self._moving or not self._conn:
                    continue

                curr_x += random.randint(-40, 40)
                curr_y += random.randint(-40, 40)
                curr_x = max(10, min(1000, curr_x))
                curr_y = max(10, min(1000, curr_y))

                if self._conn:
                    try:
                        await self._conn.send(
                            "Runtime.evaluate",
                            {"expression": f"if (window.__browserAutoCursorMove) window.__browserAutoCursorMove({curr_x}, {curr_y})"},
                        )
                    except Exception:
                        pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Cursor loop error: {e}")

    @staticmethod
    async def highlight_element(runtime: RuntimeDomain, selector: str) -> None:
        """Draw a visual highlight (red border + semi-transparent overlay) on an element and move the global cursor to it."""
        safe_selector = json.dumps(selector)
        try:
            await runtime.evaluate(f"""
                (() => {{
                    const el = document.querySelector({safe_selector});
                    if (!el) return;

                    el.dataset._prevOutline = el.style.outline || '';
                    el.dataset._prevOutlineOffset = el.style.outlineOffset || '';
                    el.dataset._prevBoxShadow = el.style.boxShadow || '';
                    el.style.outline = '3px solid #FF0000';
                    el.style.outlineOffset = '2px';
                    el.style.boxShadow = '0 0 0 4px rgba(255, 0, 0, 0.25)';
                    el.setAttribute('data-ready-ai-highlight', 'true');

                    if (window.__browserAutoCursorMove) {{
                        const rect = el.getBoundingClientRect();
                        const centerX = rect.left + rect.width / 2;
                        const centerY = rect.top + rect.height / 2;
                        window.__browserAutoCursorMove(centerX, centerY);
                    }}
                }})()
            """)
        except Exception:
            pass

    @staticmethod
    async def clear_highlight(runtime: RuntimeDomain) -> None:
        """Remove visual highlight from the previously highlighted element."""
        try:
            await runtime.evaluate("""
                (() => {
                    const el = document.querySelector('[data-ready-ai-highlight]');
                    if (el) {
                        el.style.outline = el.dataset._prevOutline || '';
                        el.style.outlineOffset = el.dataset._prevOutlineOffset || '';
                        el.style.boxShadow = el.dataset._prevBoxShadow || '';
                        el.removeAttribute('data-ready-ai-highlight');
                        delete el.dataset._prevOutline;
                        delete el.dataset._prevOutlineOffset;
                        delete el.dataset._prevBoxShadow;
                    }
                })()
            """)
        except Exception:
            pass


def extract_selector(action_desc: str) -> str | None:
    """Extract CSS selector from an action description like 'Clicked element: #btn'."""
    match = re.search(r"element(?:\s+via\s+\w+\s+fallback)?:\s*(.+?)(?:\n|$)", action_desc)
    if match:
        selector = match.group(1).strip()
        if selector and not selector.startswith("[Failed") and not selector.startswith("[Error"):
            return selector
    return None
