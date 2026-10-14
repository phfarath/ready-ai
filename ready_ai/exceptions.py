"""Public exception taxonomy for the ``ready_ai`` SDK.

Every failure the SDK can surface maps onto one of these classes, so a
consumer distinguishes *why* a run stopped without parsing strings:

- ``FlowValidationError`` — a public model or its translation onto the
  engine was rejected (shape/constraint problem; subclass of
  ``ValueError``).
- ``PolicyViolationError`` — the engine refused the run on policy
  grounds, fail-closed (foreign paused checkpoint, out-of-range resume
  index, credential auto-login under zero-LLM replay, resuming a run
  that is not paused).
- ``UnknownProfileError`` — a ``BrowserOptions.profile`` reference is
  not in the façade's allowlist registry.
- ``ConnectionLostError`` — the CDP connection dropped and could not
  be recovered (disconnect, exhausted circuit breaker).
- ``RunTimeoutError`` — the run exceeded its ``timeout_s`` budget.
- ``RunCancelledError`` — the caller cancelled the in-flight run task.

All inherit from ``ReadyAIError``; SDK methods never leak internal
``src.*`` exception types to callers.
"""

from __future__ import annotations


class ReadyAIError(Exception):
    """Base class for all public ``ready_ai`` SDK errors."""


class FlowValidationError(ReadyAIError, ValueError):
    """A public model or its translation onto the engine was rejected."""


class PolicyViolationError(ReadyAIError):
    """The engine refused the run on policy grounds (fail-closed)."""


class UnknownProfileError(ReadyAIError, ValueError):
    """A ``BrowserOptions.profile`` reference is not registered."""


class ConnectionLostError(ReadyAIError):
    """The CDP connection dropped and could not be recovered."""


class RunTimeoutError(ReadyAIError):
    """Raised when a flow exceeds its ``timeout_s`` budget."""


class RunCancelledError(ReadyAIError):
    """Raised when the caller cancels the in-flight run task."""
