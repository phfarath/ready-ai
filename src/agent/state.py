"""
State management and checkpointing for AgenticLoop runs.
Allows runs to be persisted to disk and resumed after a crash or for API polling.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DocStepState:
    """State of a single generated documentation step."""
    number: int
    title: str
    action_description: str
    annotation: str
    screenshot_path: str
    status: str = "completed"
    status_reason: str = ""
    # Baselines for self-healing documentation (doc-as-test)
    baseline_dom_hash: str = ""
    baseline_url: str = ""


@dataclass
class RunState:
    """The complete state of an AgenticLoop run."""
    run_id: str
    goal: str
    url: str
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
                data['doc_steps'] = [DocStepState(**s) for s in data['doc_steps']]
                
            return cls(**data)
        except FileNotFoundError:
            return None
        except Exception as e:
            logger.error(f"Failed to load checkpoint from {path}: {e}")
            return None
