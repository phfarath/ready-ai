"""
CDP Runtime Domain — JavaScript execution helpers.

V2: Enhanced get_interactive_elements with aria-label, role, data-testid
for robust LLM selector generation.
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
        Get a detailed summary of interactive elements on the page.
        Exposes aria-label, role, data-testid, data-cy for robust selector generation.

        Returns:
            JSON string listing buttons, links, inputs, selects with stable selector info
        """
        js = """
        (() => {
            const elements = [];
            const selectors = [
                'a', 'button', 'input', 'select', 'textarea',
                '[role="button"]', '[role="link"]', '[role="tab"]',
                '[role="menuitem"]', '[onclick]', '[data-testid]', '[data-cy]'
            ];
            const seen = new WeakSet();
            
            function processElement(el, context) {
                if (seen.has(el)) return;
                seen.add(el);
                
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return;
                
                // Build the BEST selector for this element (priority order)
                let bestSelector = null;
                const ariaLabel = el.getAttribute('aria-label');
                const testId = el.getAttribute('data-testid') || el.getAttribute('data-cy');
                const role = el.getAttribute('role');
                
                if (el.id) {
                    bestSelector = '#' + el.id;
                } else if (testId) {
                    bestSelector = `[data-testid="${testId}"]`;
                } else if (ariaLabel) {
                    bestSelector = `[aria-label="${ariaLabel}"]`;
                } else if (el.name) {
                    bestSelector = `${el.tagName.toLowerCase()}[name="${el.name}"]`;
                } else if (el.type && el.tagName === 'INPUT') {
                    bestSelector = `input[type="${el.type}"]`;
                }
                
                elements.push({
                    tag: el.tagName.toLowerCase(),
                    type: el.type || null,
                    text: (el.innerText || el.value || el.placeholder || '').slice(0, 80).trim(),
                    id: el.id || null,
                    name: el.name || null,
                    href: el.href || null,
                    ariaLabel: ariaLabel || null,
                    role: role || null,
                    testId: testId || null,
                    selector: bestSelector,
                    visible: rect.top >= 0 && rect.top < window.innerHeight,
                    inShadowDom: context.shadow || false,
                    inIframe: context.iframe || false
                });
            }
            
            function traverseRoot(root, context) {
                for (const sel of selectors) {
                    try {
                        const matches = root.querySelectorAll(sel);
                        matches.forEach((el, i) => {
                            if (i > 25) return;
                            processElement(el, context);
                        });
                    } catch(e) { /* selector may fail in some contexts */ }
                }
                
                // Traverse shadow roots
                try {
                    root.querySelectorAll('*').forEach(el => {
                        if (el.shadowRoot) {
                            traverseRoot(el.shadowRoot, { ...context, shadow: true });
                        }
                    });
                } catch(e) {}
                
                // Traverse same-origin iframes
                try {
                    root.querySelectorAll('iframe').forEach(iframe => {
                        try {
                            const iframeDoc = iframe.contentDocument;
                            if (iframeDoc) {
                                traverseRoot(iframeDoc, { ...context, iframe: true });
                            }
                        } catch(e) { /* cross-origin iframe — skip */ }
                    });
                } catch(e) {}
            }
            
            traverseRoot(document, { shadow: false, iframe: false });
            return JSON.stringify(elements.slice(0, 60));
        })()
        """
        result = await self.evaluate(js)
        return str(result) if result else "[]"

    async def find_element_by_text(self, text: str, tag_filter: str = "*") -> Optional[str]:
        """
        Find an element by its visible text content.
        Last-resort selector resolution.

        Args:
            text: The visible text to search for
            tag_filter: Optional tag name filter (e.g., 'button', 'a')

        Returns:
            A CSS selector or XPath-style identifier, or None
        """
        safe_text = json.dumps(text)
        safe_tag = json.dumps(tag_filter)
        js = f"""
        (() => {{
            const els = document.querySelectorAll({safe_tag});
            for (const el of els) {{
                if (el.innerText?.trim().includes({safe_text})) {{
                    if (el.id) return '#' + el.id;
                    if (el.getAttribute('aria-label')) return '[aria-label="' + el.getAttribute('aria-label') + '"]';
                    return null;  // Can't build stable selector, use click_text action
                }}
            }}
            return null;
        }})()
        """
        result = await self.evaluate(js)
        return result if isinstance(result, str) else None
