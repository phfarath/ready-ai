"""
CDP Runtime Domain — JavaScript execution helpers.
"""

import json
import logging
from typing import Any, Optional

from .connection import CDPConnection

logger = logging.getLogger(__name__)


class RuntimeDomain:
    """Execute JavaScript in the browser context via Runtime.evaluate."""

    def __init__(self, conn: CDPConnection):
        self._conn = conn

    async def evaluate(self, expression: str) -> Any:
        """
        Evaluate a JavaScript expression and return its value.

        Args:
            expression: JS expression string

        Returns:
            The evaluated value (primitive types only via CDP serialization)
        """
        result = await self._conn.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )

        remote_obj = result.get("result", {})
        if remote_obj.get("type") == "undefined":
            return None
        if "value" in remote_obj:
            return remote_obj["value"]

        # For complex objects, try to serialize
        subtype = remote_obj.get("subtype")
        if subtype == "null":
            return None

        return remote_obj.get("description", str(remote_obj))

    async def query_selector(self, selector: str) -> Optional[str]:
        """
        Find an element by CSS selector and return its objectId.

        Args:
            selector: CSS selector

        Returns:
            Remote object ID, or None if not found
        """
        result = await self._conn.send(
            "Runtime.evaluate",
            {
                "expression": f"document.querySelector('{selector}')",
                "returnByValue": False,
            },
        )
        remote_obj = result.get("result", {})
        if remote_obj.get("subtype") == "null" or remote_obj.get("type") == "undefined":
            return None
        return remote_obj.get("objectId")

    async def get_element_text(self, selector: str) -> str:
        """
        Get the innerText of an element.

        Args:
            selector: CSS selector

        Returns:
            The element's text content, or empty string if not found
        """
        result = await self.evaluate(
            f"document.querySelector('{selector}')?.innerText || ''"
        )
        return str(result) if result else ""

    async def get_visible_text(self) -> str:
        """Get all visible text on the page (body.innerText)."""
        result = await self.evaluate("document.body?.innerText || ''")
        return str(result) if result else ""

    async def get_element_attributes(self, selector: str) -> dict:
        """Get all attributes of an element as a dict."""
        js = f"""
        (() => {{
            const el = document.querySelector('{selector}');
            if (!el) return null;
            const attrs = {{}};
            for (const attr of el.attributes) {{
                attrs[attr.name] = attr.value;
            }}
            return attrs;
        }})()
        """
        result = await self.evaluate(js)
        return result if isinstance(result, dict) else {}

    async def get_interactive_elements(self) -> str:
        """
        Get a summary of interactive elements on the page.
        Useful for LLM context about what actions are possible.

        Returns:
            JSON string listing buttons, links, inputs, selects
        """
        js = """
        (() => {
            const elements = [];
            const selectors = ['a', 'button', 'input', 'select', 'textarea', '[role="button"]', '[onclick]'];
            
            for (const sel of selectors) {
                document.querySelectorAll(sel).forEach((el, i) => {
                    if (i > 20) return;  // limit per type
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) return;  // hidden
                    
                    elements.push({
                        tag: el.tagName.toLowerCase(),
                        type: el.type || null,
                        text: (el.innerText || el.value || el.placeholder || '').slice(0, 80),
                        id: el.id || null,
                        name: el.name || null,
                        href: el.href || null,
                        selector: el.id ? '#' + el.id : 
                                  el.name ? `${el.tagName.toLowerCase()}[name="${el.name}"]` :
                                  null
                    });
                });
            }
            return JSON.stringify(elements.slice(0, 50));
        })()
        """
        result = await self.evaluate(js)
        return str(result) if result else "[]"
