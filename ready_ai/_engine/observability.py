"""
Observability — structured logging, metrics, and tracing for ready-ai.

Provides a lightweight instrumentation layer with:
- Span: async context manager for timing operations
- Metrics: in-memory counters and histograms
- RunContext: contextvars-based implicit context
- @traced decorator for automatic span creation
- JSON structured logging via stdlib logging

Designed with OTel-ready interfaces: swap backend in this file only.
"""

import functools
import json
import logging
import os
import re
import time
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger("observability")

# ---------------------------------------------------------------------------
# JSON Logging Formatter
# ---------------------------------------------------------------------------

# Keys whose values MUST be redacted from log output (case-insensitive match).
_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {"password", "token", "api_key", "cookies", "authorization"}
)

_REDACTED_VALUE = "***REDACTED***"

# Quickly identify strings that look like URLs with a scheme authority.
_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")

# http(s) URLs embedded inside free text (stack traces, assertion
# messages, console args). The trailing class excludes common quoting /
# bracketing so the regex does not swallow sentence punctuation.
_EMBEDDED_URL_RE = re.compile(r"https?://[^\s\"'<>\)\]\}]+")


def _safe_netloc(parsed) -> str:
    """``host[:port]`` with userinfo dropped; malformed ports tolerated."""
    host = parsed.hostname or ""
    try:
        port = parsed.port
    except ValueError:
        port = None
    return f"{host}:{port}" if port is not None else host


def _strip_url_userinfo(value: str) -> str:
    """Strip ``user:pass@`` userinfo from an http/https URL.

    Non-URL strings or URLs without userinfo are returned unchanged.
    """
    if not _URL_SCHEME_RE.match(value):
        return value
    try:
        parsed = urlparse(value)
    except (ValueError, TypeError):
        return value
    if parsed.scheme in ("http", "https") and (
        parsed.username or parsed.password
    ):
        return urlunparse(parsed._replace(netloc=_safe_netloc(parsed)))
    return value


def _scrub_embedded_urls(value: str) -> str:
    """Reduce every http(s) URL inside free text to scheme://host/path.

    Free-text fields (failure reasons, stack traces, console args) carry
    raw URLs with query strings, userinfo and fragment secrets. Each
    embedded URL is rewritten to its safe form; an URL that cannot be
    parsed is replaced by a placeholder rather than kept verbatim.
    """
    def _sub(match: "re.Match[str]") -> str:
        raw = match.group(0)
        try:
            parsed = urlparse(raw)
            if not parsed.hostname:
                return "[redacted-url]"
            return urlunparse(
                (parsed.scheme, _safe_netloc(parsed), parsed.path, "", "", "")
            )
        except (TypeError, ValueError):
            return "[redacted-url]"

    return _EMBEDDED_URL_RE.sub(_sub, value)


def _redact(value: Any, *, scrub_embedded: bool = False) -> Any:
    """Recursively redact sensitive keys and sanitize URLs in ``value``.

    - Dict keys matching ``_SENSITIVE_KEYS`` (case-insensitive) have their
      values replaced with ``***REDACTED***``.
    - String values have URL userinfo stripped.
    - With ``scrub_embedded`` (flight-recorder bundles only) every
      embedded http(s) URL is additionally reduced to scheme/host/path —
      stricter than logs, which keep queries for debuggability.
    - Nested dicts and lists are traversed recursively.
    """
    if isinstance(value, dict):
        return {
            k: (
                _REDACTED_VALUE
                if k.lower() in _SENSITIVE_KEYS
                else _redact(v, scrub_embedded=scrub_embedded)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, scrub_embedded=scrub_embedded) for item in value]
    if isinstance(value, str):
        value = _strip_url_userinfo(value)
        return _scrub_embedded_urls(value) if scrub_embedded else value
    return value


class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON for structured analysis.

    Sensitive keys in ``record.structured`` are redacted and URLs have
    their userinfo stripped before serialization.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Attach structured extras if present (with sensitive data redacted)
        if hasattr(record, "structured"):
            payload["data"] = _redact(record.structured)
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass
class _HistogramBucket:
    values: list[float] = field(default_factory=list)

    def record(self, value: float) -> None:
        self.values.append(value)

    def summary(self) -> dict[str, Any]:
        if not self.values:
            return {"count": 0}
        sorted_vals = sorted(self.values)
        n = len(sorted_vals)
        return {
            "count": n,
            "min": sorted_vals[0],
            "max": sorted_vals[-1],
            "mean": sum(sorted_vals) / n,
            "p50": sorted_vals[n // 2],
            "p95": sorted_vals[int(n * 0.95)] if n >= 20 else sorted_vals[-1],
        }


class Metrics:
    """In-memory counters and histograms. Thread-safe for single-process use."""

    def __init__(self) -> None:
        self._counters: dict[str, float] = {}
        self._histograms: dict[str, _HistogramBucket] = {}
        self._counter_attrs: dict[str, dict[str, float]] = {}

    def increment(self, name: str, value: float = 1.0, **attrs: Any) -> None:
        """Increment a counter, optionally keyed by attributes."""
        self._counters[name] = self._counters.get(name, 0.0) + value
        if attrs:
            if name not in self._counter_attrs:
                self._counter_attrs[name] = {}
            attr_key = json.dumps(attrs, sort_keys=True)
            self._counter_attrs[name][attr_key] = (
                self._counter_attrs[name].get(attr_key, 0.0) + value
            )

    def record(self, name: str, value: float) -> None:
        """Record a value in a histogram."""
        if name not in self._histograms:
            self._histograms[name] = _HistogramBucket()
        self._histograms[name].record(value)

    def get_counter(self, name: str) -> float:
        return self._counters.get(name, 0.0)

    def get_counter_by_attr(self, name: str) -> dict[str, float]:
        return dict(self._counter_attrs.get(name, {}))

    def summary(self) -> dict[str, Any]:
        """Full metrics summary."""
        result: dict[str, Any] = {"counters": dict(self._counters)}
        if self._counter_attrs:
            result["counters_by_attr"] = {
                name: {k: v for k, v in attrs.items()}
                for name, attrs in self._counter_attrs.items()
            }
        result["histograms"] = {
            name: bucket.summary()
            for name, bucket in self._histograms.items()
        }
        return result

    def reset(self) -> None:
        self._counters.clear()
        self._histograms.clear()
        self._counter_attrs.clear()


# ---------------------------------------------------------------------------
# Span
# ---------------------------------------------------------------------------


@dataclass
class Span:
    """Lightweight tracing span that logs start/end as structured JSON."""

    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    start_time: float = 0.0
    end_time: Optional[float] = None
    status: str = "ok"
    children: list["Span"] = field(default_factory=list)

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def duration_ms(self) -> float:
        if self.end_time is None:
            return (time.monotonic() - self.start_time) * 1000
        return (self.end_time - self.start_time) * 1000

    async def __aenter__(self) -> "Span":
        self.start_time = time.monotonic()
        ctx = _current_context()
        if ctx:
            ctx.push_span(self)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.end_time = time.monotonic()
        if exc_type is not None:
            self.status = "error"
            self.set_attribute("error", str(exc_val))
        ctx = _current_context()
        if ctx:
            ctx.pop_span()
        _log_span(self)

    def __enter__(self) -> "Span":
        self.start_time = time.monotonic()
        ctx = _current_context()
        if ctx:
            ctx.push_span(self)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.end_time = time.monotonic()
        if exc_type is not None:
            self.status = "error"
            self.set_attribute("error", str(exc_val))
        ctx = _current_context()
        if ctx:
            ctx.pop_span()
        _log_span(self)


def _log_span(span: Span) -> None:
    record_data = {
        "event": "span_end",
        "span": span.name,
        "duration_ms": round(span.duration_ms(), 2),
        "status": span.status,
    }
    if span.attributes:
        record_data["attributes"] = span.attributes
    _structured_log(logging.DEBUG, f"Span {span.name} completed", record_data)


# ---------------------------------------------------------------------------
# RunContext (contextvars)
# ---------------------------------------------------------------------------

_run_context_var: ContextVar[Optional["RunContext"]] = ContextVar(
    "run_context", default=None
)


class RunContext:
    """Per-run context carrying run_id, metrics, and span stack."""

    def __init__(self, run_id: str = "unknown") -> None:
        self.run_id = run_id
        self.metrics = Metrics()
        self._span_stack: list[Span] = []
        self._start_time = time.monotonic()

    def push_span(self, span: Span) -> None:
        if self._span_stack:
            self._span_stack[-1].children.append(span)
        self._span_stack.append(span)

    def pop_span(self) -> Optional[Span]:
        if self._span_stack:
            return self._span_stack.pop()
        return None

    @property
    def current_span(self) -> Optional[Span]:
        return self._span_stack[-1] if self._span_stack else None

    def elapsed_s(self) -> float:
        return time.monotonic() - self._start_time

    def run_summary(self, status: str = "FINISHED", **extra: Any) -> dict[str, Any]:
        """Generate a structured run summary combining metrics and extra data."""
        summary: dict[str, Any] = {
            "event": "run_complete",
            "run_id": self.run_id,
            "status": status,
            "duration_s": round(self.elapsed_s(), 2),
        }
        summary.update(extra)

        m = self.metrics
        summary["llm_calls"] = int(m.get_counter("llm.calls"))
        summary["llm_tokens"] = {
            "prompt": int(m.get_counter("llm.prompt_tokens")),
            "completion": int(m.get_counter("llm.completion_tokens")),
        }
        summary["llm_cost_usd"] = round(m.get_counter("llm.cost_usd"), 6)

        # Cost by role
        cost_attrs = m.get_counter_by_attr("llm.cost_usd")
        if cost_attrs:
            summary["llm_cost_by_role"] = {
                json.loads(k).get("role", "unknown"): round(v, 6)
                for k, v in cost_attrs.items()
            }

        summary["steps_executed"] = int(m.get_counter("step.executed"))
        summary["steps_succeeded"] = int(m.get_counter("step.succeeded"))
        summary["steps_failed"] = int(m.get_counter("step.failed"))
        summary["total_retries"] = int(m.get_counter("step.retries"))

        summary["recovery_events"] = {
            "spa_drift": int(m.get_counter("recovery.spa_drift")),
            "local_recovery": int(m.get_counter("recovery.local_recovery")),
            "crash": int(m.get_counter("recovery.crash")),
        }

        # Histograms
        histograms = m.summary().get("histograms", {})
        if "llm.latency_ms" in histograms:
            summary["llm_latency_ms"] = histograms["llm.latency_ms"]
        if "step.latency_ms" in histograms:
            summary["step_latency_ms"] = histograms["step.latency_ms"]

        return summary


def init_run_context(run_id: str = "unknown") -> RunContext:
    """Create and set a new RunContext for the current async context."""
    ctx = RunContext(run_id=run_id)
    _run_context_var.set(ctx)
    return ctx


def _current_context() -> Optional[RunContext]:
    return _run_context_var.get()


def get_metrics() -> Optional[Metrics]:
    ctx = _current_context()
    return ctx.metrics if ctx else None


def get_run_context() -> Optional[RunContext]:
    return _current_context()


# ---------------------------------------------------------------------------
# @traced decorator
# ---------------------------------------------------------------------------


def traced(
    name: Optional[str] = None,
    **static_attrs: Any,
):
    """Decorator to automatically wrap an async function in a Span."""

    def decorator(func):
        span_name = name or func.__name__

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            span = Span(name=span_name, attributes=dict(static_attrs))
            async with span:
                return await func(*args, **kwargs)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def setup_observability(verbose: bool = False, json_output: bool = True) -> None:
    """Configure structured logging for the application."""
    level = logging.DEBUG if verbose else logging.INFO

    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers
    root.handlers.clear()

    handler = logging.StreamHandler()
    if json_output:
        handler.setFormatter(JSONFormatter(datefmt="%Y-%m-%dT%H:%M:%S"))
    else:
        fmt = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
        handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))
    handler.setLevel(level)
    root.addHandler(handler)

    # Suppress noisy libraries
    for lib in ("websockets", "httpcore", "httpx", "litellm"):
        logging.getLogger(lib).setLevel(logging.WARNING)


def _structured_log(level: int, msg: str, data: dict[str, Any]) -> None:
    """Emit a log record with structured data attached."""
    record = logger.makeRecord(
        name=logger.name,
        level=level,
        fn="",
        lno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    record.structured = data  # type: ignore[attr-defined]
    logger.handle(record)


def log_event(event: str, **data: Any) -> None:
    """Log a structured event at INFO level."""
    _structured_log(logging.INFO, event, {"event": event, **data})


# ---------------------------------------------------------------------------
# Flight Recorder — sanitized terminal-failure bundles (READY-AI-T-9)
# ---------------------------------------------------------------------------
#
# A terminal failure must be diagnosable WITHOUT re-running the flow. Every
# section below is best-effort and bounded: no cookies, credentials, response
# bodies, or raw DOM ever enter the bundle — DOM goes through the same
# sanitizer used for LLM context, URLs keep only scheme/host/path, and the
# CDP event history already retains only routing/outcome fields
# (``_history_safe_event``).

ENV_FR_DISABLED = "READY_AI_FR_DISABLED"
ENV_FR_MAX_DOM_CHARS = "READY_AI_FR_MAX_DOM_CHARS"
ENV_FR_MAX_EVENTS = "READY_AI_FR_MAX_EVENTS"
ENV_FR_MAX_SCREENSHOT_BYTES = "READY_AI_FR_MAX_SCREENSHOT_BYTES"
ENV_FR_MAX_BUNDLES = "READY_AI_FR_MAX_BUNDLES"
ENV_FR_DIR = "READY_AI_FLIGHT_RECORDER_DIR"

_FR_CONSOLE_METHODS = (
    "Runtime.consoleAPICalled",
    "Runtime.exceptionThrown",
    "Log.entryAdded",
)


@dataclass(frozen=True)
class FlightRecorderConfig:
    """Bounded capture limits, all overridable via ``READY_AI_FR_*`` env."""

    enabled: bool = True
    max_dom_chars: int = 8_000
    max_events: int = 100
    max_screenshot_bytes: int = 1_500_000
    max_bundles: int = 20

    @classmethod
    def from_env(cls) -> "FlightRecorderConfig":
        def _int(name: str, default: int) -> int:
            try:
                value = int(os.environ.get(name, "") or default)
            except ValueError:
                return default
            return value if value > 0 else default

        return cls(
            enabled=os.environ.get(ENV_FR_DISABLED, "").lower()
            not in ("1", "true", "yes", "on"),
            max_dom_chars=_int(ENV_FR_MAX_DOM_CHARS, 8_000),
            max_events=_int(ENV_FR_MAX_EVENTS, 100),
            max_screenshot_bytes=_int(
                ENV_FR_MAX_SCREENSHOT_BYTES, 1_500_000
            ),
            max_bundles=_int(ENV_FR_MAX_BUNDLES, 20),
        )


def _bundle_url(value: Any) -> str:
    """Keep scheme/host/path only — queries and userinfo carry secrets."""
    if not isinstance(value, str) or not value:
        return ""
    try:
        parsed = urlparse(value)
    except (TypeError, ValueError):
        return ""
    return urlunparse(
        (parsed.scheme, _safe_netloc(parsed), parsed.path, "", "", "")
    )


def _console_entry(sequence: int, event: dict[str, Any]) -> dict[str, Any]:
    """Compact one sanitized history event into a console/diagnostic row."""
    params = event.get("params") or {}
    method = event.get("method") or ""
    entry: dict[str, Any] = {
        "seq": sequence,
        "method": method,
        "session_id": event.get("sessionId") or params.get("sessionId"),
        "target_id": event.get("targetId") or params.get("targetId"),
    }
    if method == "Runtime.consoleAPICalled":
        entry["type"] = params.get("type")
        args = params.get("args")
        if isinstance(args, list):
            entry["text"] = " ".join(
                str(a.get("value", a.get("type", "")))
                for a in args
                if isinstance(a, dict)
            )[:500]
    elif method == "Runtime.exceptionThrown":
        details = params.get("exceptionDetails") or {}
        exception = details.get("exception") or {}
        entry["text"] = (
            exception.get("description") or details.get("text") or ""
        )[:500]
        entry["url"] = details.get("url")
        entry["line"] = details.get("lineNumber")
    elif method == "Log.entryAdded":
        log_entry = params.get("entry") or {}
        entry["level"] = log_entry.get("level")
        entry["text"] = str(log_entry.get("text") or "")[:500]
        entry["url"] = log_entry.get("url")
    return entry


def _prune_bundles(directory: Path, max_bundles: int) -> None:
    """Retain only the ``max_bundles`` most recent bundle files."""
    try:
        bundles = sorted(
            directory.glob("*_failure.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in bundles[max_bundles:]:
            stale.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug(f"Flight recorder pruning skipped: {exc}")


async def capture_failure_bundle(
    *,
    output_dir: str,
    run_id: str,
    kind: str = "flow",
    flow: Optional[str] = None,
    status: str = "FAILED",
    failure_reason: Optional[str] = None,
    step: Optional[dict[str, Any]] = None,
    initial_url: Optional[str] = None,
    page: Any = None,
    runtime: Any = None,
    connection: Any = None,
    run_ctx: Optional[RunContext] = None,
    config: Optional[FlightRecorderConfig] = None,
) -> Optional[Path]:
    """Capture a sanitized diagnostic bundle for a terminal failure.

    Returns the bundle path, or None when disabled or the write fails.
    Never raises: each section is guarded and records ``capture_errors``
    so a crashed browser cannot prevent the rest of the bundle.
    """
    cfg = config or FlightRecorderConfig.from_env()
    if not cfg.enabled:
        return None

    errors: list[str] = []

    async def _try(label: str, coro) -> Any:
        try:
            return await coro
        except Exception as exc:  # noqa: BLE001 — recorder must not raise
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
            return None

    bundle: dict[str, Any] = {
        "schema": "ready-ai.flight-recorder/v1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "kind": kind,
        "flow": flow,
        "status": status,
        "failure_reason": failure_reason,
        "step": step,
        "sanitization": (
            "sensitive keys redacted; urls reduced to scheme/host/path; "
            "no cookies, credentials, response bodies, or raw DOM"
        ),
    }

    # Live URL — the flow's start URL is always known; the live one is a
    # best-effort probe that often survives a step failure.
    bundle["url"] = {"initial": _bundle_url(initial_url)}
    if runtime is not None:
        live = await _try("url", runtime.evaluate("window.location.href"))
        bundle["url"]["current"] = _bundle_url(live)

    if page is not None:
        dom = await _try("dom", page.get_dom_html(max_length=cfg.max_dom_chars))
        if dom is not None:
            bundle["dom_excerpt"] = dom
        shot = await _try("screenshot", page.screenshot())
        if isinstance(shot, str) and shot:
            approx_bytes = len(shot) * 3 // 4
            if approx_bytes <= cfg.max_screenshot_bytes:
                bundle["screenshot_b64"] = shot
            else:
                bundle["screenshot_b64"] = (
                    f"omitted: {approx_bytes}B exceeds "
                    f"{cfg.max_screenshot_bytes}B cap"
                )

    if runtime is not None:
        fingerprint = await _try(
            "fingerprint", runtime.get_state_fingerprint()
        )
        if fingerprint:
            bundle["state_fingerprint"] = fingerprint[:2_000]

    if connection is not None:
        state = getattr(connection, "state", None)
        bundle["connection"] = {
            "state": getattr(state, "value", state),
            "consecutive_failures": getattr(
                connection, "_consecutive_failures", None
            ),
        }
        try:
            context = getattr(page, "context", None) if page is not None else None
            if context is not None and getattr(context, "is_empty", True):
                context = None
            history = getattr(connection, "_event_history", None) or []
            events = [
                (seq, ev)
                for seq, ev in history
                if context is None or context.matches(ev)
            ]
            bundle["console"] = [
                _console_entry(seq, ev)
                for seq, ev in events
                if ev.get("method") in _FR_CONSOLE_METHODS
            ][-cfg.max_events :]
            bundle["network_events"] = [
                ev
                for _, ev in events
                if str(ev.get("method") or "").startswith("Network.")
            ][-cfg.max_events :]
        except Exception as exc:  # noqa: BLE001
            errors.append(f"events: {type(exc).__name__}: {exc}")

    if page is not None and hasattr(page, "http_failures_since"):
        try:
            failures = page.http_failures_since(0)
            bundle["network_failures"] = [
                asdict(item) if hasattr(item, "__dataclass_fields__") else item
                for item in (failures or [])
            ]
        except Exception as exc:  # noqa: BLE001
            errors.append(f"network_failures: {type(exc).__name__}: {exc}")

    if run_ctx is not None:
        try:
            bundle["timings"] = run_ctx.run_summary(status=status)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"timings: {type(exc).__name__}: {exc}")

    bundle["capture_errors"] = errors

    try:
        directory = Path(
            os.environ.get(ENV_FR_DIR) or Path(output_dir) / "flight_recorder"
        )
        directory.mkdir(parents=True, exist_ok=True)
        # run_id is user-controlled (CLI --run-id, FlowSpec) and flows
        # straight into the filename — sanitize it so a pathological id
        # cannot escape the recorder directory.
        safe_run_id = re.sub(r"[^A-Za-z0-9_-]+", "_", str(run_id)) or "run"
        path = directory / f"{safe_run_id}_failure.json"
        path.write_text(
            json.dumps(
                _redact(bundle, scrub_embedded=True), indent=2, default=str
            ),
            encoding="utf-8",
        )
        _prune_bundles(directory, cfg.max_bundles)
        log_event("flight_recorder_bundle", run_id=run_id, path=str(path))
        return path
    except Exception as exc:  # noqa: BLE001 — the recorder never raises
        logger.warning(f"Failed to write flight recorder bundle: {exc}")
        return None
