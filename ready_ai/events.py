"""Public run-event schema for the ``ready_ai`` SDK.

``RunHandle.events()`` yields ``RunEvent`` objects in strict ``seq``
order, emitted at engine boundaries:

    run_started → (step_started → retry* → step_finished)* →
    checkpoint_paused | checkpoint_resumed → run_finished

Ordering contract
-----------------
- ``seq`` is a strictly increasing per-run counter starting at 1.
- ``run_started`` is always the first event, ``run_finished`` always the
  last (``status``: ``passed`` | ``failed`` | ``paused`` |
  ``pending_confirmation`` | ``cancelled`` | ``error``).
- ``step_*`` events nest inside their ``step_index``; ``retry`` events
  sit between ``step_started`` and ``step_finished`` of the enclosing
  step and carry ``detail.action``/``detail.attempt``.
- ``checkpoint_paused``/``checkpoint_resumed`` mark a human checkpoint
  (``await_human``) suspending or releasing the run.

Payloads are scrubbed before leaving the engine boundary: no CDP
objects, cookies, DOM contents, response bodies, or URL credentials —
only plain serializable fields.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

EVENT_TYPES = frozenset(
    {
        "run_started",
        "step_started",
        "retry",
        "step_finished",
        "checkpoint_paused",
        "checkpoint_resumed",
        "run_finished",
    }
)
"""Known ``RunEvent.type`` values; unknown types are tolerated forward-compatibly."""


class RunEvent(BaseModel):
    """One ordered, sanitized run event emitted by the engine."""

    model_config = ConfigDict(extra="ignore")

    seq: int = Field(..., ge=1, description="Strictly increasing per-run counter")
    type: str = Field(..., description="Event type; see EVENT_TYPES")
    run_id: str = Field(..., description="Run this event belongs to")
    ts: float = Field(..., description="Epoch seconds when the event was emitted")
    step_index: Optional[int] = Field(
        None, description="1-based step index for step/checkpoint events"
    )
    step_name: Optional[str] = Field(None, description="Declared step name, if any")
    status: Optional[str] = Field(
        None,
        description="Step status (step_finished) or run status (run_finished)",
    )
    detail: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extra scrubbed fields (reason, action, attempt, ...)",
    )
