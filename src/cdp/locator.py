
"""Locator resolution and NodeRef with semantic fallback, AX tree integration,
and action validation (visibility, stability, hit-target).
"""

import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

_RESOLUTION_ORDER = [
    "data-testid",
    "role+name",
    "text",
    "css",
]


class LocatorPath:
    def __init__(self, method: str, selector: Optional[str] = None, detail: Optional[str] = None):
        self.method = method
        self.selector = selector
        self.detail = detail or method

    def __str__(self) -> str:
        s = self.selector or self.detail
        return f"{self.method}({s})"

    def __repr__(self) -> str:
        return f"LocatorPath(method={self.method!r}, selector={self.selector!r}, detail={self.detail!r})"


class NodeRef:
    def __init__(
        self,
        node_id: Optional[int] = None,
        selector: Optional[str] = None,
        path: Optional["LocatorPath"] = None,
        element: Optional[Any] = None,
        ax_role: Optional[str] = None,
        ax_name: Optional[str] = None,
    ):
        self.node_id = node_id
        self.selector = selector
        self.path = path or LocatorPath("unknown", selector=selector)
        self.element = element
        self.ax_role = ax_role
        self.ax_name = ax_name

    def __repr__(self) -> str:
        return (
            f"NodeRef(node_id={self.node_id}, selector={self.selector!r}, "
            f"path={self.path!r}, ax_role={self.ax_role!r}, ax_name={self.ax_name!r})"
        )

    def is_visible(self, conn) -> bool:
        if self.element is not None:
            try:
                rect = self.element.get("rect", {}) or {}
                if rect:
                    return rect.get("width", 0) > 0 and rect.get("height", 0) > 0
            except Exception:
                pass
        return True


class Locator:
    def __init__(self, conn):
        self._conn = conn

    async def resolve(
        self,
        *,
        role: Optional[str] = None,
        name: Optional[str] = None,
        text: Optional[str] = None,
        test_id: Optional[str] = None,
        css: Optional[str] = None,
        tag: str = "*",
    ) -> Optional[NodeRef]:
        if test_id:
            sel = f"[data-testid=\"{test_id}\"]"
            ref = await self._try_selector(sel, method="data-testid", detail=test_id)
            if ref:
                ref.path = LocatorPath("data-testid", selector=sel, detail=test_id)
                return ref

        if role or name:
            if role and name:
                sel = f"[role=\"{role}\"]:is([aria-label*=\"{name}\" i], [name*=\"{name}\" i])"
            elif role:
                sel = f"[role=\"{role}\"]"
            elif name:
                sel = "*"
            else:
                sel = tag
            ref = await self._try_selector(sel, method="role+name", detail=f"{role or ''}+{name or ''}")
            if ref:
                detail = f"role={role}" if role else (f"name={name}" if name else "")
                ref.path = LocatorPath("role+name", selector=sel, detail=detail)
                return ref

        if text:
            safe_text = repr(text)
            js = f"""
            (() => {{
                const text = {safe_text};
                const tags = ["a", "button", "input", "select", "textarea", "[role=\"button\"]", "[role=\"link\"]"];
                for (const tag of tags) {{
                    const els = document.querySelectorAll(tag);
                    for (const el of els) {{
                        if ((el.innerText || el.value || el.getAttribute("aria-label") || "").trim().includes(text)) {{
                            return el;
                        }}
                    }}
                }}
                return null;
            }})()
            """
            try:
                result = await self._conn.send("Runtime.evaluate", {"expression": js, "returnByValue": False})
                obj_id = result.get("result", {}).get("objectId")
                if obj_id:
                    ref = NodeRef(node_id=obj_id, selector=f"text-match={text}")
                    ref.path = LocatorPath("text", selector=f"text-match={text}", detail=text)
                    return ref
            except Exception:
                pass

        if css:
            ref = await self._try_selector(css, method="css", detail=css)
            if ref:
                ref.path = LocatorPath("css", selector=css, detail=css)
                return ref
        elif text:
            # Fallback: try to find any element whose text includes the target
            safe_text_esc = text.replace('"', '\\"')
            sel_fallback = f"[data-testid*='{safe_text_esc}']"
            ref_fb = await self._try_selector(sel_fallback, method="css", detail=f"fallback-text={text}")
            if ref_fb:
                ref_fb.path = LocatorPath("css", selector=sel_fallback, detail=f"fallback-text={text}")
                return ref_fb

        return None

    async def _try_selector(self, selector: str, method: str = "css", detail: Optional[str] = None) -> Optional[NodeRef]:
        try:
            doc = await self._conn.send("DOM.getDocument")
            root_id = doc["root"]["nodeId"]
            result = await self._conn.send("DOM.querySelector", {"nodeId": root_id, "selector": selector})
            node_id = result.get("nodeId", 0)
            if node_id != 0:
                return NodeRef(node_id=node_id, selector=selector)
        except Exception:
            pass
        return None


class ActionValidator:
    def __init__(self, conn):
        self._conn = conn

    async def check_visible(self, selector: str) -> bool:
        try:
            js = f"""(() => {{
                const el = document.querySelector("{selector}");
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
            }})();"""
            result = await self._conn.send("Runtime.evaluate", {"expression": js, "returnByValue": True})
            val = result.get("result", {}).get("value")
            return bool(val)
        except Exception as exc:
            logger.debug(f"Visibility check failed for {selector}: {exc}")
            return False

    async def check_enabled(self, selector: str) -> bool:
        try:
            js = f"""(() => {{
                const el = document.querySelector("{selector}");
                if (!el) return false;
                return !el.disabled && !el.hasAttribute("disabled") && !(el.getAttribute("aria-disabled") === "true");
            }})();"""
            result = await self._conn.send("Runtime.evaluate", {"expression": js, "returnByValue": True})
            val = result.get("result", {}).get("value")
            return bool(val)
        except Exception as exc:
            logger.debug(f"Enabled check failed for {selector}: {exc}")
            return False

    async def check_stable(self, selector: str, previous_rect: Optional[Dict] = None) -> bool:
        try:
            current_rect = await self._get_rect(selector)
            if previous_rect is None:
                return True
            dx = abs((current_rect.get("x", 0) or 0) - (previous_rect.get("x", 0) or 0))
            dy = abs((current_rect.get("y", 0) or 0) - (previous_rect.get("y", 0) or 0))
            return dx < 5 and dy < 5
        except Exception:
            return False

    async def check_hit_target(self, selector: str) -> bool:
        try:
            js = f"""(() => {{
                const el = document.querySelector("{selector}");
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                const cx = rect.left + rect.width / 2;
                const cy = rect.top + rect.height / 2;
                const topEl = document.elementFromPoint(cx, cy);
                return topEl === el || el.contains(topEl);
            }})();"""
            result = await self._conn.send("Runtime.evaluate", {"expression": js, "returnByValue": True})
            val = result.get("result", {}).get("value")
            return bool(val)
        except Exception as exc:
            logger.debug(f"Hit-target check failed for {selector}: {exc}")
            return False

    async def _get_rect(self, selector: str) -> Optional[Dict]:
        try:
            js = f"""(() => {{
                const el = document.querySelector("{selector}");
                if (!el) return null;
                const rect = el.getBoundingClientRect();
                return {{x: rect.x, y: rect.y, width: rect.width, height: rect.height}};
            }})();"""
            result = await self._conn.send("Runtime.evaluate", {"expression": js, "returnByValue": True})
            val = result.get("result", {}).get("value")
            return val if isinstance(val, dict) else None
        except Exception:
            return None
