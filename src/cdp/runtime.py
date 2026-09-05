"""
CDP Runtime Domain — JavaScript execution helpers.

V2: Enhanced get_interactive_elements with aria-label, role, data-testid
for robust LLM selector generation.

P1-2: get_interactive_elements now sanitizes its output by default. The
sanitization policy mirrors sanitize_html(): sensitive values (passwords,
credit-card numbers, autocomplete hints, PII keywords) are always
redacted; long non-sensitive values are truncated to
READY_AI_DOM_VALUE_MAX chars (default 200). Raw mode
(READY_AI_RAW_DOM=true) bypasses the cosmetic passes but NOT the
sensitive layer — call with raw=True only after you've considered
the LGPD/PCI/GDPR implications.
"""

import json
import logging
from typing import Any, Optional

from ..observability import get_metrics
from .connection import CDPConnection
from .sanitize import (
    is_raw_mode,
    resolve_value_max,
    sanitize_interactive_element,
)

logger = logging.getLogger(__name__)


class RuntimeDomain:
    """Execute JavaScript in the browser context via Runtime.evaluate."""

    def __init__(self, conn: CDPConnection):
        self._conn = conn

    async def evaluate(
        self, expression: str, session_id: Optional[str] = None
    ) -> Any:
        """
        Evaluate a JavaScript expression and return its value.

        Args:
            expression: JS expression string
            session_id: Explicit CDP session (tab/popup/OOPIF). None uses
                the connection default (primary tab).

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
            session_id=session_id,
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

    async def query_selector(
        self, selector: str, session_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Find an element by CSS selector and return its objectId.

        Args:
            selector: CSS selector
            session_id: Explicit CDP session (tab/popup/OOPIF).

        Returns:
            Remote object ID, or None if not found
        """
        result = await self._conn.send(
            "Runtime.evaluate",
            {
                "expression": f"document.querySelector({json.dumps(selector)})",
                "returnByValue": False,
            },
            session_id=session_id,
        )
        remote_obj = result.get("result", {})
        if remote_obj.get("subtype") == "null" or remote_obj.get("type") == "undefined":
            return None
        return remote_obj.get("objectId")

    async def get_element_text(
        self, selector: str, session_id: Optional[str] = None
    ) -> str:
        """
        Get the innerText of an element.

        Args:
            selector: CSS selector
            session_id: Explicit CDP session (tab/popup/OOPIF).

        Returns:
            The element's text content, or empty string if not found
        """
        result = await self.evaluate(
            f"document.querySelector({json.dumps(selector)})?.innerText || ''",
            session_id=session_id,
        )
        return str(result) if result else ""

    async def get_visible_text(
        self, session_id: Optional[str] = None
    ) -> str:
        """Get all visible text on the page (body.innerText)."""
        result = await self.evaluate(
            "document.body?.innerText || ''", session_id=session_id
        )
        return str(result) if result else ""

    async def get_state_fingerprint(self) -> str:
        """
        Return a fingerprint of page state that includes both visible text
        and the values of form fields. Used by the executor to detect whether
        an action mutated the page — `body.innerText` alone misses input
        value changes (typing into a controlled React input does not alter
        innerText).
        """
        js = """
        (() => {
            const text = document.body?.innerText || '';
            // Only visible, user-editable fields; password/file/hidden are
            // redacted so sensitive values never enter the fingerprint, and
            // invisible/disabled fields don't create false state churn.
            const fields = Array.from(
                document.querySelectorAll(
                    'input:not([type="hidden"]):not([type="file"]), textarea, select'
                )
            ).filter(e => {
                if (e.disabled || e.readOnly) return false;
                const rect = e.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return false;
                const style = window.getComputedStyle(e);
                if (style.visibility === 'hidden' || style.display === 'none') return false;
                return true;
            }).map(e => {
                const key = e.name || e.id || e.type || e.tagName;
                let val;
                if (e.type === 'password') {
                    // length only — signals change without leaking chars
                    val = '[REDACTED:' + (e.value || '').length + ']';
                } else if (e.type === 'checkbox' || e.type === 'radio') {
                    val = e.checked ? '1' : '0';
                } else {
                    val = e.value || '';
                }
                return key + '=' + val;
            }).join('|');
            return text + '\\n__fields__:' + fields;
        })()
        """
        result = await self.evaluate(js)
        return str(result) if result else ""

    async def get_element_attributes(
        self, selector: str, session_id: Optional[str] = None
    ) -> dict:
        """Get all attributes of an element as a dict."""
        safe_sel = json.dumps(selector)
        js = f"""
        (() => {{
            const el = document.querySelector({safe_sel});
            if (!el) return null;
            const attrs = {{}};
            for (const attr of el.attributes) {{
                attrs[attr.name] = attr.value;
            }}
            return attrs;
        }})()
        """
        result = await self.evaluate(js, session_id=session_id)
        return result if isinstance(result, dict) else {}

    def resolve_target_session(self, ref) -> str:
        """Resolve a flow-level tab reference to its CDP session id.

        Raises RuntimeError naming the known targets when unresolvable
        (fail-closed at dispatch/assert time, never silently primary).
        """
        try:
            return self._conn.targets.resolve(ref).session_id
        except (KeyError, AttributeError) as exc:
            raise RuntimeError(str(exc)) from exc

    async def get_interactive_elements(self, raw: Optional[bool] = None) -> str:
        """
        Get a detailed summary of interactive elements on the page.
        Exposes aria-label, role, data-testid, data-cy for robust selector generation.

        Args:
            raw: When True, skip ALL sanitization of the per-element
                text/value/placeholder/ariaLabel fields. Sensitive
                redaction still happens. When None (default), the
                env var READY_AI_RAW_DOM is consulted. P1-2 default
                behaviour (sanitized) is what the planner should see
                in production.

        Returns:
            JSON string listing buttons, links, inputs, selects with
            stable selector info. Sensitive values are redacted;
            long non-sensitive values are truncated.
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
        return self._post_sanitize_interactive(
            str(result) if result else "[]",
            raw=raw,
        )

    @staticmethod
    def _post_sanitize_interactive(
        payload: str, *, raw: Optional[bool] = None
    ) -> str:
        """Sanitize a get_interactive_elements payload before returning.

        Decodes the JSON array, runs each element dict through
        sanitize_interactive_element(), strips the per-element
        ``_redactions`` key, and re-serializes. On any decoding
        error we return the input verbatim (the caller logged the
        failure in this case is a degraded but visible mode).
        """
        if raw is None:
            raw = is_raw_mode()

        try:
            elements = json.loads(payload) if payload else []
        except (json.JSONDecodeError, TypeError):
            return payload or "[]"
        if not isinstance(elements, list):
            return payload or "[]"

        value_max = resolve_value_max()
        metrics = get_metrics()
        out: list[dict] = []
        for el in elements:
            if not isinstance(el, dict):
                out.append(el)
                continue
            sanitized = sanitize_interactive_element(
                el, raw=raw, value_max=value_max
            )
            redactions = sanitized.pop("_redactions", {})
            if metrics is not None and redactions:
                for k, v in redactions.items():
                    metrics.increment(
                        f"cdp.sanitize.interactive.{k}",
                        value=v,
                        source="interactive",
                    )
            out.append(sanitized)
        return json.dumps(out)

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
