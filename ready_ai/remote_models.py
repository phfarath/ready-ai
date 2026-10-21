"""Typed wire models for the ready-ai remote client (API B parte 2).

Mirrors the server's response shapes (``src/api/models.py`` + history
endpoints) without importing ``src.*`` — the SDK never depends on
server internals. ``extra="ignore"`` everywhere: the server may add
fields without breaking released SDKs. Invalid shapes surface as
``FlowValidationError`` at the call site (see ``remote._validate``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "RemoteHealth",
    "RemoteReadiness",
    "RemoteRunStatus",
    "RemoteRunListItem",
    "RemoteRunList",
    "RemoteFlowStepResult",
    "RemoteFlowResult",
    "RemoteDiff",
    "RemoteDocSet",
    "RemoteDocList",
    "RemoteDocVersionStatus",
    "RemoteExport",
    "RemoteHistoryRecord",
    "RemoteHistory",
    "RemoteAggregates",
]


class _Wire(BaseModel):
    model_config = ConfigDict(extra="ignore")


class RemoteHealth(_Wire):
    status: str
    service: str
    version: Optional[str] = None
    timestamp: Optional[str] = None


class RemoteReadiness(_Wire):
    status: str
    checks: Dict[str, Any] = Field(default_factory=dict)
    timestamp: Optional[str] = None


class RemoteRunStatus(_Wire):
    run_id: str
    status: str
    goal: str = ""
    url: str = ""
    executed_steps: int = 0
    total_planned_steps: int = 0
    last_known_url: Optional[str] = None
    error: Optional[str] = None


class RemoteRunListItem(_Wire):
    run_id: str
    status: str
    goal: str = ""
    url: str = ""
    executed_steps: int = 0
    total_planned_steps: int = 0
    app_version: Optional[str] = None
    git_commit: Optional[str] = None
    deployed_at: Optional[str] = None
    created_at: Optional[str] = None


class RemoteRunList(_Wire):
    total: int
    runs: List[RemoteRunListItem] = Field(default_factory=list)


class RemoteFlowStepResult(_Wire):
    index: int
    name: Optional[str] = None
    status: str = "passed"
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    asserts: List[Dict[str, Any]] = Field(default_factory=list)
    extracted: List[Dict[str, Any]] = Field(default_factory=list)
    attempts: int = 1
    failure_reason: str = ""
    skipped_asserts: int = 0
    skipped_extractions: int = 0


class RemoteFlowResult(_Wire):
    run_id: str
    flow: Optional[str] = None
    url: str = ""
    status: str = "passed"
    steps: List[RemoteFlowStepResult] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)
    failure_reason: Optional[str] = None


class RemoteDiff(_Wire):
    run_id: str
    previous_version: Optional[str] = None
    current_version: Optional[str] = None
    changelog: str = ""
    diff_json: Dict[str, Any] = Field(default_factory=dict)


class RemoteDocSet(_Wire):
    run_id: str
    version: Optional[str] = None
    goal: str = ""
    url: str = ""
    status: str = ""
    files: List[str] = Field(default_factory=list)
    generated_at: Optional[str] = None


class RemoteDocList(_Wire):
    total: int
    docs: List[RemoteDocSet] = Field(default_factory=list)


class RemoteDocVersionStatus(_Wire):
    version: str
    run_id: str
    status: str
    steps_count: int = 0
    screenshots_count: int = 0
    has_manifest: bool = False
    has_metrics: bool = False
    last_tested: Optional[str] = None
    test_status: Optional[str] = None


class RemoteExport(_Wire):
    run_id: str
    format: str
    output_dir: str
    files_created: List[str] = Field(default_factory=list)
    success: bool = True


class RemoteHistoryRecord(_Wire):
    run_id: str
    goal: str = ""
    status: str = ""
    steps_count: int = 0
    llm_tokens: Any = 0
    duration_sec: float = 0.0
    app_version: Optional[str] = None
    timestamp: Optional[str] = None


class RemoteHistory(_Wire):
    total: int
    records: List[RemoteHistoryRecord] = Field(default_factory=list)


class RemoteAggregates(_Wire):
    total_runs: int = 0
    pass_rate: Optional[float] = None
    broken_rate: Optional[float] = None
    drift_events: Optional[int] = None
    auto_healed: Optional[int] = None
    total_steps: Optional[int] = None
    avg_steps_per_run: Optional[float] = None
    total_tokens: Optional[int] = None
    avg_tokens_per_run: Optional[float] = None
    total_duration_sec: Optional[float] = None
    avg_duration_sec: Optional[float] = None
