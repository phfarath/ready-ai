"""Declared mutations: YAML config → JS applied via CDP ``Runtime.evaluate``.

Mutation kinds (the ground truth for the scorecard):

- ``semantic``: a real product change a good detector MUST flag —
  rename a button, move a panel, remove an element, change state attrs.
- ``noise``: cosmetic churn a good detector should NOT flag —
  timestamps, rotating avatars, ephemeral banners.

Each mutation compiles to a JS expression that returns truthy when the
mutation was actually applied (selector found, op executed) — falsy means
the declaration is stale and the outcome is recorded as ``applied=False``
instead of silently polluting TP/FP counts.

Value placeholders (resolved per-apply, so noise mutations really churn):
``{{now}}`` → ISO-8601 timestamp, ``{{rand}}`` → 8 random hex chars.
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

KINDS = ("semantic", "noise")
OPS = ("set_text", "set_attribute", "remove", "move_after", "insert_banner", "js")

_OPS_REQUIRING_SELECTOR = ("set_text", "set_attribute", "remove", "move_after")
_OPS_REQUIRING_VALUE = ("set_text", "set_attribute")


def _js_str(value: str) -> str:
    return json.dumps(value)


def _substitute(value: str) -> str:
    """Resolve ``{{now}}``/``{{rand}}`` placeholders per application."""
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    return value.replace("{{now}}", now).replace("{{rand}}", secrets.token_hex(4))


@dataclass(frozen=True)
class Mutation:
    """One declared mutation from the YAML config."""

    id: str
    kind: str
    op: str
    selector: Optional[str] = None
    value: str = ""
    attr: Optional[str] = None
    to: Optional[str] = None
    js: Optional[str] = None
    description: str = ""

    @property
    def is_semantic(self) -> bool:
        return self.kind == "semantic"

    def apply_js(self) -> str:
        """JS expression (truthy on success) applied via Runtime.evaluate."""
        if self.op == "js":
            assert self.js is not None
            return _substitute(self.js)

        sel = _js_str(self.selector or "")
        head = (
            f"(()=>{{const el=document.querySelector({sel});"
            "if(!el)return false;"
        )
        if self.op == "set_text":
            return head + f"el.textContent={_js_str(_substitute(self.value))};return true;}})()"
        if self.op == "set_attribute":
            return head + (
                f"el.setAttribute({_js_str(self.attr or '')},"
                f"{_js_str(_substitute(self.value))});return true;}})()"
            )
        if self.op == "remove":
            return head + "el.remove();return true;})()"
        if self.op == "move_after":
            return (
                f"(()=>{{const el=document.querySelector({sel});"
                f"const t=document.querySelector({_js_str(self.to or '')});"
                "if(!el||!t)return false;"
                "t.insertAdjacentElement('afterend',el);return true;})()"
            )
        if self.op == "insert_banner":
            # Appends a dismissable-looking banner with a unique id. When a
            # ``selector`` is given the banner lands inside it, otherwise it
            # prepends to <body> like a real promo/notice banner.
            banner_id = f"ra-mutation-{self.id}"
            target = self.selector or "body"
            return (
                f"(()=>{{const host=document.querySelector({_js_str(target)});"
                "if(!host)return false;"
                f"if(document.getElementById({_js_str(banner_id)}))return true;"
                "const b=document.createElement('div');"
                f"b.id={_js_str(banner_id)};"
                "b.style.cssText='display:block;padding:24px 32px;"
                "background:#7c3aed;color:#fff;font:600 18px/1.4 sans-serif;"
                "text-align:center;position:relative;z-index:9999';"
                f"b.textContent={_js_str(_substitute(self.value) or 'Notice')};"
                "host.prepend(b);return true;})()"
            )
        raise ValueError(f"Unknown mutation op: {self.op}")


@dataclass(frozen=True)
class DrillConfig:
    """Parsed mutation-drill YAML configuration."""

    app_url: str
    threshold: float = 0.95
    settle_ms: int = 150
    mutations: tuple = field(default_factory=tuple)


def load_config(config_path: str | Path) -> DrillConfig:
    """Load and validate a mutation-drill YAML config.

    Raises ``ValueError`` on malformed config — drill misconfigurations
    must be loud, never silently skipped.
    """
    path = Path(config_path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level mapping expected")

    app_url = raw.get("app_url", "")
    threshold = float(raw.get("threshold", 0.95))
    settle_ms = int(raw.get("settle_ms", 150))
    if not 0.0 < threshold <= 1.0:
        raise ValueError(f"{path}: threshold must be in (0, 1], got {threshold}")

    entries = raw.get("mutations")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: 'mutations' must be a non-empty list")

    mutations = []
    seen_ids: set[str] = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: mutation #{i} must be a mapping")
        mut = _parse_mutation(entry, where=f"{path}: mutation #{i}")
        if mut.id in seen_ids:
            raise ValueError(f"{path}: duplicate mutation id {mut.id!r}")
        seen_ids.add(mut.id)
        mutations.append(mut)

    return DrillConfig(
        app_url=app_url,
        threshold=threshold,
        settle_ms=settle_ms,
        mutations=tuple(mutations),
    )


def _parse_mutation(entry: dict, where: str) -> Mutation:
    mid = entry.get("id")
    if not mid or not isinstance(mid, str):
        raise ValueError(f"{where}: 'id' is required")

    kind = entry.get("kind")
    if kind not in KINDS:
        raise ValueError(f"{where}: kind must be one of {KINDS}, got {kind!r}")

    op = entry.get("op", "js" if entry.get("js") else "")
    if op not in OPS:
        raise ValueError(f"{where}: op must be one of {OPS}, got {op!r}")

    selector = entry.get("selector")
    if op in _OPS_REQUIRING_SELECTOR and not selector:
        raise ValueError(f"{where}: op {op!r} requires 'selector'")
    if op in _OPS_REQUIRING_VALUE and "value" not in entry:
        raise ValueError(f"{where}: op {op!r} requires 'value'")
    if op == "set_attribute" and not entry.get("attr"):
        raise ValueError(f"{where}: op 'set_attribute' requires 'attr'")
    if op == "move_after" and not entry.get("to"):
        raise ValueError(f"{where}: op 'move_after' requires 'to'")
    if op == "js" and not entry.get("js"):
        raise ValueError(f"{where}: op 'js' requires 'js'")

    # Type-check fields compiled into JS — a non-string value (e.g.
    # `selector: 123`) must fail loudly at parse time, not as a silent
    # JS SyntaxError inside the page.
    for field_name in ("selector", "attr", "to", "js"):
        val = entry.get(field_name)
        if val is not None and not isinstance(val, str):
            raise ValueError(f"{where}: {field_name!r} must be a string")
    value = entry.get("value", "")
    if not isinstance(value, (str, int, float)):
        raise ValueError(f"{where}: 'value' must be a scalar")

    return Mutation(
        id=mid,
        kind=kind,
        op=op,
        selector=selector,
        value=str(value),
        attr=entry.get("attr"),
        to=entry.get("to"),
        js=entry.get("js"),
        description=str(entry.get("description", "")),
    )
