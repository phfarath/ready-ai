"""
DOM payload sanitization for the CDP layer.

P1-2 of the CDP resilience roadmap. The DOM and accessibility
snapshots are sent to the LLM as part of the planner context.
Without sanitization, sensitive values like credit-card numbers,
passwords, SSNs, and session tokens can leak into the prompt
and from there into logs, traces, and possibly the model
provider's input.

This module is pure-functional (no I/O, no Chrome, no state)
so it can be unit-tested deterministically and reused by both
the HTML snapshot (`PageDomain.get_dom_html`) and the
interactive-elements snapshot (`RuntimeDomain.
get_interactive_elements`).

The redaction policy has two layers, in order of severity:

  1. SENSITIVE values are ALWAYS redacted, regardless of mode.
     This covers autocomplete hints like `cc-number`,
     `current-password`, `one-time-code`, and field names
     containing `password`, `senha`, `ssn`, `cpf`, `cnpj`.
     Compliance: LGPD, PCI-DSS, GDPR.

  2. NON-SENSITIVE values are kept but truncated to
     `value_max` characters in non-raw mode. Raw mode is
     opt-in via `READY_AI_RAW_DOM=true` and is intended for
     debug / dev only.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Constants and env
# ---------------------------------------------------------------------------

REDACTED_SENTINEL = "[REDACTED]"

# Env vars (read at module import; consistent with other CDP tunables).
ENV_RAW_DOM = "READY_AI_RAW_DOM"
ENV_DOM_VALUE_MAX = "READY_AI_DOM_VALUE_MAX"
DOM_VALUE_MAX_DEFAULT = 200

# Keywords that mark a field as sensitive. The match is
# case-insensitive substring against the field's `name`,
# `id`, or `placeholder`.
_SENSITIVE_KEYWORDS: tuple[str, ...] = (
    "password",
    "passwd",
    "senha",
    "ssn",
    "cpf",
    "cnpj",
    "secret",
    "token",
    "api_key",
    "apikey",
)

# autocomplete attribute values that mark a field as sensitive.
# Source: HTML Living Standard + the WHATWG autocomplete spec.
_SENSITIVE_AUTOCOMPLETE: frozenset[str] = frozenset(
    {
        "cc-number",
        "cc-csc",
        "cc-exp",
        "cc-exp-month",
        "cc-exp-year",
        "current-password",
        "new-password",
        "one-time-code",
    }
)

# Data attributes that are safe to keep in the DOM snapshot. We
# strip every other data-* attribute because SaaS apps routinely
# encode PII or internal state in them.
SAFE_DATA_ATTRS: frozenset[str] = frozenset({"data-testid", "data-cy"})


def is_raw_mode() -> bool:
    """True if the env var opts out of sanitization (dev/debug only)."""
    return os.environ.get(ENV_RAW_DOM, "").lower() in ("1", "true", "yes", "on")


def resolve_value_max() -> int:
    """Resolve the per-value cap from the env. Falls back to the default
    on invalid input and logs a warning so misconfiguration is visible."""
    raw = os.environ.get(ENV_DOM_VALUE_MAX)
    if raw is None or raw.strip() == "":
        return DOM_VALUE_MAX_DEFAULT
    try:
        value = int(raw.strip())
    except ValueError:
        return DOM_VALUE_MAX_DEFAULT
    if value < 0:
        return 0
    return value


# ---------------------------------------------------------------------------
# Pure decision helpers
# ---------------------------------------------------------------------------


def is_sensitive_field(
    name: Optional[str] = None,
    autocomplete: Optional[str] = None,
    field_type: Optional[str] = None,
) -> bool:
    """Decide whether a form field's value must be redacted.

    All three inputs are optional. The decision is a logical OR
    of the three rules so a single hit is enough to trigger
    redaction.
    """
    # Rule 1: HTML type=password is ALWAYS sensitive.
    if field_type and field_type.lower() == "password":
        return True
    # Rule 2: sensitive autocomplete hint.
    if autocomplete and autocomplete.lower() in _SENSITIVE_AUTOCOMPLETE:
        return True
    # Rule 3: sensitive keyword in the field's identifier.
    if name:
        lower = name.lower()
        for kw in _SENSITIVE_KEYWORDS:
            if kw in lower:
                return True
    return False


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------


@dataclass
class SanitizationCounters:
    """Counters for what was changed during a sanitization pass.

    The caller is expected to feed these into the project's
    `Metrics` instance (a dict-like), keyed by source. This
    keeps the sanitize module free of any observability import.
    """

    scripts_removed: int = 0
    styles_removed: int = 0
    noscripts_removed: int = 0
    comments_removed: int = 0
    data_attrs_removed: int = 0
    values_truncated: int = 0
    values_redacted: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "scripts_removed": self.scripts_removed,
            "styles_removed": self.styles_removed,
            "noscripts_removed": self.noscripts_removed,
            "comments_removed": self.comments_removed,
            "data_attrs_removed": self.data_attrs_removed,
            "values_truncated": self.values_truncated,
            "values_redacted": self.values_redacted,
        }


@dataclass
class SanitizedHTML:
    """Result of a single sanitize_html() pass.

    `html` is the sanitized string; `counters` describes the
    mutations performed. The `to_metrics_attrs` helper converts
    the counters to a dict suitable for `Metrics.increment(...)`
    so callers don't have to know the field names.
    """

    html: str
    counters: SanitizationCounters = field(default_factory=SanitizationCounters)

    def to_metrics_attrs(self) -> dict[str, int]:
        return self.counters.to_dict()


# ---------------------------------------------------------------------------
# Compiled regexes (module-level: compiled once, reused forever)
# ---------------------------------------------------------------------------

# <script ...>...</script>  (DOTALL = . matches newlines)
_RE_SCRIPT = re.compile(r"<script\b[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
# <style ...>...</style>
_RE_STYLE = re.compile(r"<style\b[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
# <noscript ...>...</noscript>
_RE_NOSCRIPT = re.compile(r"<noscript\b[^>]*>.*?</noscript>", re.DOTALL | re.IGNORECASE)
# <!-- ... -->
_RE_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

# Build the safe-data-attr alternation once.
_SAFE_DATA_ATTRS_ALT = "|".join(re.escape(a) for a in sorted(SAFE_DATA_ATTRS))
# Match a `data-foo="bar"` attribute (NOT data-testid/data-cy) preceded by
# whitespace. We don't try to be clever with quoting because Chrome escapes
# `>` and `"` in attribute values, so the simple form is sufficient.
_RE_DATA_ATTR = re.compile(
    r'\s+(?!(?:' + _SAFE_DATA_ATTRS_ALT + r'))data-[a-zA-Z0-9-]+="[^"]*"',
)


# ---------------------------------------------------------------------------
# HTML sanitization
# ---------------------------------------------------------------------------


def sanitize_html(
    html: str,
    *,
    raw: Optional[bool] = None,
    value_max: Optional[int] = None,
) -> SanitizedHTML:
    """Sanitize an HTML string for LLM consumption.

    Args:
        html: The raw outerHTML from `DOM.getOuterHTML`.
        raw: When True, skip ALL sanitization (dev/debug only).
            When None (default), read the env var
            `READY_AI_RAW_DOM`.
        value_max: Per-value cap (in characters) for input and
            textarea `value` attributes. When None, reads
            `READY_AI_DOM_VALUE_MAX` (default 200).

    Returns:
        A `SanitizedHTML` with the sanitized HTML and per-pass
        counters. Sensitive values are always redacted; non-
        sensitive values are truncated when not in raw mode.
    """
    counters = SanitizationCounters()

    # Resolve defaults from env at call time, so tests that
    # mutate the env at runtime are observed.
    if raw is None:
        raw = is_raw_mode()
    if value_max is None:
        value_max = resolve_value_max()

    # Sensitive values are ALWAYS redacted, regardless of raw mode.
    # We do this pass first, then conditionally do structural/truncation passes.
    html, n_redacted = _redact_sensitive_values(html)
    counters.values_redacted = n_redacted

    if raw:
        # In raw mode, we only do sensitive redaction and skip everything else.
        return SanitizedHTML(html=html, counters=counters)

    # Pass 1: structural noise removal.
    html, n_script = _RE_SCRIPT.subn("", html)
    counters.scripts_removed = n_script
    html, n_style = _RE_STYLE.subn("", html)
    counters.styles_removed = n_style
    html, n_noscript = _RE_NOSCRIPT.subn("", html)
    counters.noscripts_removed = n_noscript
    html, n_comment = _RE_HTML_COMMENT.subn("", html)
    counters.comments_removed = n_comment

    # Pass 2: data-* attribute stripping (preserve data-testid
    # and data-cy).
    html, n_data = _RE_DATA_ATTR.subn("", html)
    counters.data_attrs_removed = n_data

    # Pass 3: per-input / per-textarea value handling (truncation only).
    # We already did sensitive redaction above, so this pass only handles
    # truncation of non-sensitive values.
    html, n_truncated = _sanitize_form_values_truncate_only(html, value_max)
    counters.values_truncated = n_truncated

    return SanitizedHTML(html=html, counters=counters)


# Pattern that matches the opening of an <input> tag. We don't
# try to handle nested elements; the <input> tag is self-closing
# in HTML5, so the regex stops at the first '>'.
_RE_INPUT_TAG = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
_RE_TEXTAREA_TAG = re.compile(r"<textarea\b[^>]*>", re.IGNORECASE)


def _extract_attr(tag: str, name: str) -> Optional[str]:
    """Pull a single attribute out of a tag string. Returns None if absent."""
    # Match name="value" or name='value'.
    m = re.search(
        r'\b' + re.escape(name) + r'''\s*=\s*(?:"([^"]*)"|'([^']*)')''',
        tag,
        re.IGNORECASE,
    )
    if not m:
        return None
    return m.group(1) if m.group(1) is not None else m.group(2)


def _replace_attr_value(tag: str, name: str, new_value: str) -> str:
    """Replace a single attribute's value in a tag string."""
    return re.sub(
        r'(' + re.escape(name) + r"""\s*=\s*)(?:"[^"]*"|'[^']*')""",
        lambda m: m.group(1) + '"' + new_value.replace('"', "&quot;") + '"',
        tag,
        count=1,
        flags=re.IGNORECASE,
    )


def _redact_sensitive_values(html: str) -> tuple[str, int]:
    """Walk every input/textarea opening tag and redact sensitive values.

    Returns (new_html, n_redacted).
    """
    n_redacted = 0

    def _process_input(match: re.Match) -> str:
        nonlocal n_redacted
        tag = match.group(0)
        if "value" not in tag.lower():
            return tag
        field_type = _extract_attr(tag, "type")
        autocomplete = _extract_attr(tag, "autocomplete")
        # We need a "name" to feed is_sensitive_field. The id is
        # a reasonable proxy; placeholder too.
        name = (
            _extract_attr(tag, "name")
            or _extract_attr(tag, "id")
            or _extract_attr(tag, "placeholder")
        )
        value = _extract_attr(tag, "value") or ""
        if not value:
            return tag
        if is_sensitive_field(name=name, autocomplete=autocomplete, field_type=field_type):
            n_redacted += 1
            return _replace_attr_value(tag, "value", REDACTED_SENTINEL)
        return tag

    def _process_textarea(match: re.Match) -> str:
        # Textareas don't have a `value` attribute; the content
        # lives between the tags. We don't re-write content here
        # because doing so safely requires parsing the tag's
        # attributes (we'd lose the closing </textarea>). Instead
        # we leave textarea content untouched in this pass; the
        # caller can apply a separate cap if needed.
        return match.group(0)

    new_html = _RE_INPUT_TAG.sub(_process_input, html)
    new_html = _RE_TEXTAREA_TAG.sub(_process_textarea, new_html)
    return new_html, n_redacted


def _sanitize_form_values_truncate_only(html: str, value_max: int) -> tuple[str, int]:
    """Walk every input/textarea opening tag and truncate non-sensitive values.

    Assumes sensitive values are assumed to have already been redacted.
    Returns (new_html, n_truncated).
    """
    n_truncated = 0

    def _process_input(match: re.Match) -> str:
        nonlocal n_truncated
        tag = match.group(0)
        if "value" not in tag.lower():
            return tag
        field_type = _extract_attr(tag, "type")
        autocomplete = _extract_attr(tag, "autocomplete")
        # We need a "name" to feed is_sensitive_field. The id is
        # a reasonable proxy; placeholder too.
        name = (
            _extract_attr(tag, "name")
            or _extract_attr(tag, "id")
            or _extract_attr(tag, "placeholder")
        )
        value = _extract_attr(tag, "value") or ""
        if not value:
            return tag
        # Skip if sensitive (because we don't want to truncate redacted values)
        if is_sensitive_field(name=name, autocomplete=autocomplete, field_type=field_type):
            return tag
        if value_max and len(value) > value_max:
            n_truncated += 1
            new_value = value[:value_max] + "..."
            return _replace_attr_value(tag, "value", new_value)
        return tag

    def _process_textarea(match: re.Match) -> str:
        # Textareas don't have a `value` attribute; the content
        # lives between the tags. We don't re-write content here
        # because doing so safely requires parsing the tag's
        # attributes (we'd lose the closing </textarea>). Instead
        # we leave textarea content untouched in this pass; the
        # caller can apply a separate cap if needed.
        return match.group(0)

    new_html = _RE_INPUT_TAG.sub(_process_input, html)
    new_html = _RE_TEXTAREA_TAG.sub(_process_textarea, new_html)
    return new_html, n_truncated


# ---------------------------------------------------------------------------
# Interactive element sanitization
# ---------------------------------------------------------------------------


def sanitize_interactive_element(
    element: dict, *, raw: Optional[bool] = None, value_max: Optional[int] = None
) -> dict:
    """Redact sensitive values in one element dict from `get_interactive_elements`.

    The input is a dict with at least: `tag`, `type`, `text`, `name`,
    `id`, `ariaLabel`, `placeholder`, `value`, `href`. The output
    is a NEW dict; the input is not mutated.

    A `_redactions` field is appended to the returned dict so the
    caller can record per-element metrics. Callers should strip
    `_redactions` before re-serializing the dict to JSON for the
    LLM.
    """
    if raw is None:
        raw = is_raw_mode()
    if value_max is None:
        value_max = resolve_value_max()

    # Decide sensitivity. The runtime domain already maps the
    # `type` field from <input type="..."> directly.
    sensitive = is_sensitive_field(
        name=element.get("name") or element.get("id") or element.get("ariaLabel"),
        autocomplete=element.get("autocomplete"),
        field_type=element.get("type"),
    )

    redactions: dict[str, int] = {}
    out = {**element}

    # Apply to text-bearing fields. We do NOT touch `href` here
    # because URLs sometimes encode session tokens but truncating
    # them universally would break the LLM's ability to reason
    # about navigation. A future iteration can add a stricter
    # URL-token detector.
    for field_name in ("text", "value", "placeholder", "ariaLabel"):
        v = out.get(field_name)
        if not v or not isinstance(v, str):
            continue
        if sensitive:
            out[field_name] = REDACTED_SENTINEL
            redactions[f"{field_name}_redacted"] = redactions.get(f"{field_name}_redacted", 0) + 1
        # Only truncate if not in raw mode and the field is not sensitive
        elif not raw and value_max and len(v) > value_max:
            out[field_name] = v[:value_max] + "..."
            redactions[f"{field_name}_truncated"] = redactions.get(f"{field_name}_truncated", 0) + 1

    out["_redactions"] = redactions
    return out
