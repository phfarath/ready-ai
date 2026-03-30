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
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("observability")

# ---------------------------------------------------------------------------
# JSON Logging Formatter
# ---------------------------------------------------------------------------


class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON for structured analysis."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Attach structured extras if present
        if hasattr(record, "structured"):
            payload["data"] = record.structured
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
