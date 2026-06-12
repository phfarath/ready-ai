"""
CDP Accessibility Domain operations.

Exposes a semantic, low-token-cost snapshot of the page for the LLM planner,
using the Chrome DevTools Protocol Accessibility domain instead of raw HTML.

This module is feature-flagged: enable via env `READY_AI_USE_AX_TREE=true`.
PII handling is dual-mode:
  * default (READY_AI_RAW_DOM != "true"): sensitive values redacted
  * dev mode  (READY_AI_RAW_DOM == "true"): full values visible

The snapshot is intentionally compact (one line per interactive node) and
bounded (max_nodes default 200) to keep prompt costs predictable.
"""

import logging
from typing import Any, Optional

from .connection import CDPConnection
from .sanitize import is_raw_mode as _is_raw_mode

logger = logging.getLogger(__name__)


# Roles we consider "interactive" — these are what the agent needs to act on.
# Mirrors common ARIA roles plus generic fallbacks.
_INTERACTIVE_ROLES = frozenset({
    "button",
    "link",
    "textbox",
    "searchbox",
    "combobox",
    "checkbox",
    "radio",
    "switch",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "tab",
    "option",
    "spinbutton",
    "slider",
})

# Roles that always get included even if not interactive (for context).
_STRUCTURAL_ROLES = frozenset({
    "navigation",
    "main",
    "banner",
    "contentinfo",
    "heading",
    "alert",
    "dialog",
    "form",
    "region",
})

# inputmode/autocomplete hints that mark a value as sensitive.
_SENSITIVE_AUTOCOMPLETE = frozenset({
    "cc-number",
    "cc-csc",
    "cc-exp",
    "cc-exp-month",
    "cc-exp-year",
    "current-password",
    "new-password",
    "one-time-code",
})

_VALUE_REDACTION_SENTINEL = "[REDACTED]"


def _format_node(node: dict[str, Any], raw: bool) -> Optional[str]:
    """
    Format a single AX node into a compact single-line string.

    Returns None for nodes we want to skip (non-interesting, no name, etc.).
    """
    props = node.get("properties") or {}
    role = props.get("role") or {}
    role_value = (role.get("value") or "").lower()

    if not role_value:
        return None

    # Filter to interactive + structural roles only.
    if role_value not in _INTERACTIVE_ROLES and role_value not in _STRUCTURAL_ROLES:
        return None

    name = (props.get("name") or {}).get("value") or ""
    description = (props.get("description") or {}).get("value") or ""

    if not name and not description and role_value not in {"navigation", "main", "banner", "contentinfo", "form", "region"}:
        return None

    # State flags.
    state_bits: list[str] = []
    for state_name in ("disabled", "expanded", "selected", "checked", "pressed", "required", "readonly", "focused"):
        s = (props.get(state_name) or {})
        if s.get("value") is True:
            state_bits.append(state_name)

    # Value handling with PII redaction.
    value = (props.get("value") or {}).get("value") or ""
    autocomplete = (props.get("autocomplete") or {}).get("value") or ""
    is_sensitive = (
        role_value == "textbox"
        and (
            autocomplete in _SENSITIVE_AUTOCOMPLETE
            or (name and any(k in name.lower() for k in ("password", "senha", "ssn", "cpf", "cnpj")))
        )
    )

    # Value handling. Two redaction layers, in order:
    #   1. Sensitive values (autocomplete hints, password/SSN/CPF keywords
    #      in the field name) are ALWAYS redacted, regardless of mode.
    #   2. Non-sensitive values are kept but truncated to 100 chars to
    #      bound prompt size. Pass `raw=True` to disable the truncation
    #      (dev-only — see READY_AI_RAW_DOM env).
    if value and is_sensitive:
        value = _VALUE_REDACTION_SENTINEL
    elif value and not raw and len(value) > 100:
        value = value[:100] + "..."

    parts: list[str] = [role_value]
    if name:
        # Truncate long names for prompt economy.
        display_name = name if len(name) <= 100 else name[:100] + "..."
        parts.append(f'"{display_name}"')
    if state_bits:
        parts.append(f"[{', '.join(state_bits)}]")
    if value:
        parts.append(f"value={value}")
    if description and description != name:
        parts.append(f"desc=\"{description[:60]}\"")

    return " ".join(parts)


async def get_ax_snapshot(
    conn: CDPConnection,
    max_nodes: int = 200,
    interesting_only: bool = True,
) -> str:
    """
    Fetch the page's accessibility tree and return a compact, LLM-friendly
    representation (one node per line, bounded to `max_nodes`).

    The caller is expected to have called `AccessibilityDomain.enable()`
    at least once per session.

    Args:
        conn: An active CDPConnection.
        max_nodes: Cap on the number of lines returned.
        interesting_only: Pass `interestingOnly` to the CDP method (filters
            out non-semantic noise at the source — cheaper on large pages).

    Returns:
        Multi-line string. Empty string on error (never raises into the agent).
    """
    raw_mode = _is_raw_mode()
    try:
        result = await conn.send(
            "Accessibility.getFullAXTree",
            {"interestingOnly": interesting_only, "max_depth": 12},
            timeout=10.0,
        )
    except Exception as exc:
        logger.debug(f"Accessibility.getFullAXTree failed: {exc}")
        return ""

    nodes = (result or {}).get("nodes") or []
    lines: list[str] = []
    for node in nodes:
        formatted = _format_node(node, raw=raw_mode)
        if formatted is None:
            continue
        lines.append(formatted)
        if len(lines) >= max_nodes:
            lines.append(f"... (truncated at {max_nodes} nodes)")
            break

    return "\n".join(lines)


class AccessibilityDomain:
    """High-level Accessibility domain operations over a CDPConnection."""

    def __init__(self, conn: CDPConnection):
        self._conn = conn

    async def enable(self) -> None:
        """Enable the Accessibility domain. Idempotent."""
        try:
            await self._conn.send("Accessibility.enable", timeout=5.0)
        except Exception as exc:
            logger.debug(f"Accessibility.enable failed: {exc}")

    async def get_snapshot(self, max_nodes: int = 200) -> str:
        """Public wrapper that re-uses the module-level helper."""
        return await get_ax_snapshot(self._conn, max_nodes=max_nodes)
