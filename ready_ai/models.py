"""Public, stable configuration and result models for the ``ready_ai`` SDK.

This module IS the serializable contract of the public SDK. It has no
dependency on ``src.*``: consumers construct and validate against these
models, and the ``ReadyAI`` façade (``ready_ai.client``) is the only
place where they are translated onto the internal engine.

Security constraints
--------------------
- Profiles are *references* (allowlisted names/paths), never cookie
  payloads or embedded secrets. ``BrowserOptions`` serializes a profile
  name, and the façade resolves it through an explicit registry.
- Flow URLs must not embed credentials (``user:pass@``) because a URL is
  a serializable value.
- ``RunResult`` sanitizes URL userinfo and relies on engine-side masking
  of typed text at the report boundary.

Versioning / forward compatibility
----------------------------------
Config models carry a ``version`` field (default ``SCHEMA_VERSION``).
Parsing is lenient by design: unknown keys are ignored
(``extra="ignore"``) and a document declaring a newer ``version`` is
still parsed with the fields this SDK understands, so older SDKs keep
reading newer documents instead of crashing. Action parameters are the
one open-ended surface (``extra="allow"``) so future executor actions
can carry new params.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1
"""Current schema version of the public SDK config models."""

_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_PROFILE_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


# ─── Effect policy ──────────────────────────────────────────────────────────


class EffectPolicy(str, Enum):
    """Side-effect ceiling enforced by ``Flow`` validation.

    ``OBSERVE`` (read-only) < ``NAVIGATE`` (observe + navigation/scrolling)
    < ``INTERACTIVE`` (full interaction: clicks, typing, keys). A flow
    declaring an action above its policy ceiling fails validation.
    """

    OBSERVE = "observe"
    NAVIGATE = "navigate"
    INTERACTIVE = "interactive"


# Actions allowed per policy. ``None`` means unlimited (interactive, and
# forward-compatible with future executor actions). ``await_human`` is
# control-plane (it pauses the run and actuates nothing), so it is allowed
# under every ceiling.
_POLICY_ALLOWED_ACTIONS: Dict[EffectPolicy, Optional[frozenset[str]]] = {
    EffectPolicy.OBSERVE: frozenset({"observe", "wait", "await_human"}),
    EffectPolicy.NAVIGATE: frozenset(
        {"observe", "wait", "navigate", "scroll", "scroll_to", "await_human"}
    ),
    EffectPolicy.INTERACTIVE: None,
}


# ─── Profile (runtime-only reference) ──────────────────────────────────────


@dataclass(frozen=True)
class Profile:
    """Runtime credential reference resolved by the ``ReadyAI`` façade.

    A profile is an *allowlist entry*, never a serializable value: it
    holds file paths / logins that the engine reads from disk at runtime.
    Profiles are registered on ``ReadyAI`` and referenced by name from
    ``BrowserOptions``; they are never part of ``Flow``, ``BrowserOptions``
    or ``RunResult`` serialization, so secrets cannot leak through the
    SDK's serializable surface.

    Attributes:
        cookies_file: Path to a cookies JSON file (a reference, not content).
        username: Login username for credential-based auto-login.
        password: Login password (resolved at runtime only).
        user_data_dir: Explicit persistent Chrome profile directory (a
            reference, not content). When set, the engine launches Chrome
            with this profile so SSO logins survive across runs, and never
            deletes it. When None, each run gets an ephemeral temp profile
            that is always cleaned up.
    """

    cookies_file: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    user_data_dir: Optional[str] = None


# ─── Declarative flow models ───────────────────────────────────────────────


class FlowAction(BaseModel):
    """A single declarative action dispatched through the engine executor.

    Action parameters are an open-ended surface (``extra="allow"``) so
    future executor actions can add parameters without a schema bump.
    """

    model_config = ConfigDict(extra="allow")

    action: str = Field(..., min_length=1, description="Executor action type")
    selector: Optional[str] = Field(None, description="CSS selector for the action target")
    text: Optional[str] = Field(None, description="Text to type or match")
    url: Optional[str] = Field(None, description="Target URL for navigate")
    key: Optional[str] = Field(None, description="Key to press for press_key")
    direction: Optional[str] = Field(None, description="Scroll direction (up/down)")
    value: Optional[str] = Field(None, description="Generic value for future actions")
    retries: Optional[int] = Field(
        None, ge=0, description="Per-action retry budget; falls back to step/flow default"
    )


class FlowAssertion(BaseModel):
    """A declarative expectation evaluated after a step's actions."""

    model_config = ConfigDict(extra="ignore")

    type: str = Field(..., description="Assertion type (url_contains, element_visible, ...)")
    expected: Any = Field(None, description="Expected value for the assertion")
    selector: Optional[str] = Field(None, description="CSS selector for element/text asserts")
    attribute: Optional[str] = Field(None, description="Attribute name for attribute_equals")
    message: Optional[str] = Field(None, description="Optional human-readable failure message")
    target: Optional[Any] = Field(
        None, description="Tab reference: index, exact targetId, or URL substring"
    )


class FlowExtraction(BaseModel):
    """A declarative data extraction performed after a step's actions."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., description="Result key for the extracted value")
    selector: str = Field(..., description="CSS selector of the element(s) to read")
    attribute: str = Field(
        "textContent",
        description="Element property to read (textContent, value, href, ...)",
    )
    multiple: bool = Field(
        False, description="Collect all matches into a list when true"
    )


class FlowStep(BaseModel):
    """A single declarative step: actions, expectations and extractions."""

    model_config = ConfigDict(extra="ignore")

    name: Optional[str] = Field(None, description="Optional step label")
    actions: List[FlowAction] = Field(default_factory=list)
    asserts: List[FlowAssertion] = Field(default_factory=list)
    extract: List[FlowExtraction] = Field(default_factory=list)
    retries: Optional[int] = Field(
        None, ge=0, description="Per-step retry budget; falls back to the flow default"
    )
    policy: Optional[str] = Field(
        None, description="Effect ceiling for this step: read | navigate | write (None inherits the flow ceiling)"
    )
    confirm: bool = Field(
        False, description="When true the step reports pending_confirmation until its idempotency key is confirmed"
    )
    irreversible: bool = Field(
        False, description="Real-world side effects; requires confirm=True (fail-closed)"
    )
    idempotency_key: Optional[str] = Field(
        None, description="Stable effect key; a resume never re-executes a confirmed key"
    )

    @field_validator("policy")
    @classmethod
    def _validate_policy(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in ("read", "navigate", "write"):
            raise ValueError("policy must be one of ['read', 'navigate', 'write']")
        return value

    @model_validator(mode="after")
    def _check_irreversible_requires_confirm(self) -> "FlowStep":
        if self.irreversible and not self.confirm:
            raise ValueError("irreversible steps require confirm=True (fail-closed)")
        return self


class Flow(BaseModel):
    """Public declarative run document (serializable, versionable).

    Executed by ``ReadyAI.run_flow`` through the engine's run-flow mode.
    Validation is enforced at construction: URL shape, positive timeouts
    and retry budgets, effect-policy ceilings and profile reference
    format. No credential/cookie material exists on this model.

    Forward compatibility: unknown keys are ignored and a ``version``
    newer than ``SCHEMA_VERSION`` is tolerated (lenient parsing).
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    version: int = Field(SCHEMA_VERSION, ge=1, description="Config schema version")
    name: Optional[str] = Field(None, description="Flow name")
    url: str = Field(..., description="Starting URL to navigate to")
    steps: List[FlowStep] = Field(
        ..., min_length=1, description="Ordered list of steps to execute"
    )
    retries: int = Field(1, ge=0, description="Default per-action retry budget")
    run_id: Optional[str] = Field(
        None,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Run identifier for checkpoints/results",
    )
    output: Optional[str] = Field(None, description="Output directory for the result JSON")
    timeout_s: float = Field(300.0, gt=0, description="Whole-run time budget in seconds")
    effect_policy: EffectPolicy = Field(
        EffectPolicy.INTERACTIVE, description="Maximum allowed side effects"
    )

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        """Reject non-http(s), hostless or credential-embedding URLs."""
        value = (value or "").strip()
        if not value:
            raise ValueError("url must not be empty")
        try:
            parsed = urlparse(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("url must be a valid absolute URL") from exc
        if parsed.scheme not in ("http", "https"):
            raise ValueError("url scheme must be http or https")
        if not parsed.netloc:
            raise ValueError("url must include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("url must not embed credentials (userinfo)")
        return value

    @model_validator(mode="after")
    def _check_effect_policy(self) -> "Flow":
        """Fail closed when a declared action exceeds the effect ceiling."""
        allowed = _POLICY_ALLOWED_ACTIONS.get(self.effect_policy)
        if allowed is None:
            return self
        for index, step in enumerate(self.steps, start=1):
            for action in step.actions:
                if action.action not in allowed:
                    raise ValueError(
                        "step %d: action %r is not allowed under "
                        "effect_policy=%s" % (index, action.action, self.effect_policy.value)
                    )
        return self


# ─── Browser options ────────────────────────────────────────────────────────


class BrowserOptions(BaseModel):
    """Browser execution context for a run (serializable, versionable).

    ``profile`` is a *reference* to an allowlisted profile registered on
    the ``ReadyAI`` façade — never cookie payloads. The reference is
    validated for shape (no path traversal, no spaces) here, and for
    registration at run time / pre-flight.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    version: int = Field(SCHEMA_VERSION, ge=1, description="Config schema version")
    headless: bool = Field(True, description="Run Chrome in headless mode")
    port: int = Field(9222, ge=1, le=65535, description="Chrome DevTools debugging port")
    profile: Optional[str] = Field(
        None, description="Reference to an allowlisted profile (never a secret)"
    )

    @field_validator("profile")
    @classmethod
    def _validate_profile(cls, value: Optional[str]) -> Optional[str]:
        """A profile is a plain reference: no traversal, no spaces/control chars."""
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("profile reference must not be empty")
        if not _PROFILE_REFERENCE_RE.fullmatch(value):
            raise ValueError(
                "profile must be a plain reference composed of letters, digits, "
                "'.', '_', '-' or '/'"
            )
        if ".." in value.replace("\\", "/").split("/"):
            raise ValueError("profile must not contain '..' path segments")
        return value


# ─── Run result (structured + sanitized) ───────────────────────────────────


def _strip_url_userinfo(value: str) -> str:
    """Strip ``user:pass@`` userinfo from http(s) URLs; others unchanged."""
    if not _URL_SCHEME_RE.match(value):
        return value
    try:
        parsed = urlparse(value)
    except (ValueError, TypeError):
        return value
    if parsed.scheme in ("http", "https") and (parsed.username or parsed.password):
        netloc = parsed.hostname or ""
        if parsed.port is not None:
            netloc = f"{netloc}:{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))
    return value


def _sanitize(value: Any) -> Any:
    """Recursively strip URL userinfo from every string in a structure."""
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return _strip_url_userinfo(value)
    return value


def _collect_artifacts(run_id: str, output_dir: str | Path) -> List[str]:
    """Only report result files that actually exist inside ``output_dir``."""
    base = Path(output_dir).resolve()
    artifacts = []
    for name in (f"{run_id}_flow_result.json", f"{run_id}_flow_metrics.json"):
        candidate = base / name
        if candidate.is_file():
            artifacts.append(str(candidate.resolve()))
    return sorted(artifacts)


class RunStep(BaseModel):
    """One executed step of a public run result."""

    model_config = ConfigDict(extra="ignore")

    index: int
    name: Optional[str] = None
    status: str = Field("passed", description="passed | failed | skipped")
    actions: List[Dict[str, Any]] = Field(
        default_factory=list, description="Step actions with masked params"
    )
    asserts: List[Dict[str, Any]] = Field(default_factory=list)
    extracted: List[Dict[str, Any]] = Field(default_factory=list)
    attempts: int = 1
    failure_reason: str = ""
    skipped_asserts: int = 0
    skipped_extractions: int = 0


class RunResult(BaseModel):
    """Structured, sanitized result of a public flow run.

    Carries ``run_id``, ``status``, ``steps``, a run summary and only the
    allowed ``artifacts`` (result files written inside the output dir).
    Sensitive material (URL credentials, typed text) is scrubbed.
    """

    model_config = ConfigDict(extra="ignore")

    run_id: str
    flow: Optional[str] = None
    url: str = ""
    status: str = Field("passed", description="passed | failed | paused")
    steps: List[RunStep] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)
    artifacts: List[str] = Field(
        default_factory=list, description="Result files written inside the output dir"
    )
    failure_reason: Optional[str] = None
    pause: Optional[Dict[str, Any]] = Field(
        None,
        description="Human-checkpoint block (reason, resume_when, checkpoint, "
        "run_id, step_index) when status is paused; operator-authored, no secrets",
    )

    @classmethod
    def from_flow_result(
        cls, data: Mapping[str, Any], output_dir: str | Path
    ) -> "RunResult":
        """Build a sanitized public result from an engine flow result dict."""
        steps = [RunStep(**_sanitize(step)) for step in data.get("steps", [])]
        return cls(
            run_id=str(data.get("run_id") or ""),
            flow=data.get("flow"),
            url=_strip_url_userinfo(str(data.get("url") or "")),
            status=str(data.get("status") or "passed"),
            steps=steps,
            summary=_sanitize(data.get("summary") or {}),
            artifacts=_collect_artifacts(data.get("run_id") or "", output_dir),
            failure_reason=data.get("failure_reason"),
            pause=_sanitize(data.get("pause")),
        )
