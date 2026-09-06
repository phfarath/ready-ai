"""
State management and checkpointing for AgenticLoop runs.
Allows runs to be persisted to disk and resumed after a crash or for API polling.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Expectation:
    """Typed post-action assertion accepted by the executor.

    The JSON shape is intentionally small so it can be supplied by an LLM or a
    future declarative flow without turning arbitrary JavaScript into an
    assertion mechanism. Unsupported shapes become a failed outcome instead of
    being silently ignored.
    """

    kind: str
    value: str = ""
    selector: Optional[str] = None
    state: str = "visible"
    mode: str = "contains"
    status: Optional[int] = None
    timeout: float = 10.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Expectation":
        if not isinstance(raw, dict) or not isinstance(raw.get("kind"), str):
            raise ValueError("expectation requires a string kind")
        timeout = raw.get("timeout", 10.0)
        try:
            timeout = float(timeout)
        except (TypeError, ValueError) as exc:
            raise ValueError("expectation timeout must be numeric") from exc
        if timeout <= 0:
            raise ValueError("expectation timeout must be positive")
        status = raw.get("status")
        if status is not None:
            try:
                status = int(status)
            except (TypeError, ValueError) as exc:
                raise ValueError("expectation status must be an integer") from exc
        return cls(
            kind=raw["kind"],
            value=str(raw.get("value", "")),
            selector=raw.get("selector"),
            state=str(raw.get("state", "visible")),
            mode=str(raw.get("mode", "contains")),
            status=status,
            timeout=timeout,
        )


@dataclass(frozen=True)
class OutcomeEvidence:
    """Serializable, sanitized evidence explaining one expectation outcome."""

    kind: str
    passed: bool
    observed: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocStepState:
    """State of a single generated documentation step."""
    # Baselines for self-healing documentation (doc-as-test)
    number: int = 0
    title: str = ""
    action_description: str = ""
    annotation: str = ""
    screenshot_path: str = ""
    status: str = "completed"
    status_reason: str = ""
    baseline_dom_hash: str = ""
    baseline_url: str = ""


@dataclass
class RunState:
    """The complete state of an AgenticLoop run."""
    run_id: str
    goal: str
    url: str
    app_version: str = ""           # Application version (e.g. "2.3.1")
    deployed_at: str = ""           # ISO timestamp when version was deployed
    git_commit: str = ""              # Git commit hash from source repo
    app_url: str = ""               # Base URL of the app being documented
    status: str = "PLANNING"  # PLANNING, PLANNED, EXECUTING, CRITIQUE, FINISHED, FAILED

    # Execution state
    planned_steps: list[str] = field(default_factory=list)
    current_step_index: int = 0
    executed_results: list[dict] = field(default_factory=list)  # Serialized StepResults

    # Doc generation state
    doc_steps: list[DocStepState] = field(default_factory=list)
    critic_notes: str = ""
    critic_score: int = 0

    # Recovery info
    last_known_url: Optional[str] = None

    # Effect policy ledger (READY-AI-T-PH2A): idempotency keys of steps
    # whose confirmed effects already ran. A resume never re-executes them.
    confirmed_effects: list[str] = field(default_factory=list)

    # Human checkpoint (READY-AI-T-PH2D-SESSIONS): a paused run-flow records
    # WHY it stopped and WHAT the human must satisfy before resuming.
    # `current_step_index` doubles as the 1-based resume index for flow
    # mode (the doc pipeline never shares a run with run_flow, so the
    # field is unambiguous per run). Only operator-authored text lives
    # here — never cookies, credentials, or typed values.
    pause_reason: str = ""
    resume_when: dict[str, Any] = field(default_factory=dict)

    def to_file(self, path: str | Path) -> None:
        """Serialize state to a JSON file."""
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(asdict(self), f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save checkpoint to {path}: {e}")

    @classmethod
    def from_file(cls, path: str | Path) -> Optional["RunState"]:
        """Load state from a JSON file."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Convert dictionary steps back to DocStepState
            if 'doc_steps' in data:
                allowed_fields = DocStepState.__dataclass_fields__.keys()
                data['doc_steps'] = [
                    DocStepState(**{k: v for k, v in s.items() if k in allowed_fields})
                    for s in data['doc_steps']
                ]

            return cls(**data)
        except FileNotFoundError:
            return None
        except Exception as e:
            logger.error(f"Failed to load checkpoint from {path}: {e}")
            return None
