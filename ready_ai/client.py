"""Public façade of the ``ready_ai`` SDK.

``ReadyAI`` is the importable entry point. It translates the stable
public models (``ready_ai.models``) onto the internal engine
(``src.agent.loop.AgenticLoop``) without exposing ``src.*`` to
consumers:

- Profiles are *references* resolved through an explicit allowlist
  registry; secrets (cookie files, credentials) stay out of every
  serializable model and are passed to the engine from the registry.
- A flow that exceeds its ``timeout_s`` budget raises
  ``RunTimeoutError`` instead of hanging.
- Results are returned as sanitized ``RunResult`` objects.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Collection, Mapping, Optional

from src.agent.loop import AgenticLoop

from .models import BrowserOptions, EffectPolicy, Flow, Profile, RunResult

logger = logging.getLogger(__name__)


class ReadyAIError(Exception):
    """Base class for public ``ready_ai`` SDK errors."""


class UnknownProfileError(ReadyAIError, ValueError):
    """Raised when a ``BrowserOptions.profile`` reference is not registered."""


class RunTimeoutError(ReadyAIError):
    """Raised when a flow exceeds its ``timeout_s`` budget."""


def _to_flow_spec(
    flow: Flow,
    *,
    run_id: str,
    headless: bool,
    model: str,
    cookies_file: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
):
    """Translate the public ``Flow`` onto the engine's ``FlowSpec``.

    Credentials arrive as resolved profile *references* (paths/logins),
    never as serializable values from the public models. Deliberately
    local: the engine's flow models are an implementation detail and
    stay out of the SDK's public surface.
    """
    from src.api.models import (
        FlowAction as _FlowAction,
        FlowAssertion as _FlowAssertion,
        FlowExtraction as _FlowExtraction,
        FlowSpec as _FlowSpec,
        FlowStepSpec as _FlowStepSpec,
    )

    steps = [
        _FlowStepSpec(
            name=step.name,
            actions=[
                _FlowAction(**action.model_dump(exclude_none=True))
                for action in step.actions
            ],
            asserts=[
                _FlowAssertion(**assertion.model_dump(exclude_none=True))
                for assertion in step.asserts
            ],
            extract=[
                _FlowExtraction(**extraction.model_dump(exclude_none=True))
                for extraction in step.extract
            ],
            retries=step.retries,
            policy=step.policy,
            confirm=step.confirm,
            irreversible=step.irreversible,
            idempotency_key=step.idempotency_key,
        )
        for step in flow.steps
    ]
    _policy_map = {
        EffectPolicy.OBSERVE: "read",
        EffectPolicy.NAVIGATE: "navigate",
        EffectPolicy.INTERACTIVE: "write",
    }
    return _FlowSpec(
        name=flow.name,
        url=flow.url,
        steps=steps,
        retries=flow.retries,
        headless=headless,
        run_id=run_id,
        output=flow.output,
        model=model,
        cookies_file=cookies_file,
        username=username,
        password=password,
        effect_policy=_policy_map[flow.effect_policy],
    )


class ReadyAI:
    """Public SDK façade over the ready-ai engine.

    Args:
        model: LLM model used by the engine (credential auto-login only in
            run-flow mode).
        output_dir: Default output directory for run results.
        profiles: Allowlist of profile references: ``{name: Profile}``,
            ``{name: "/path/to/cookies.json"}`` or ``{name: None}``.
            Values are references only — cookies/credentials are never
            serializable through the SDK models.
        browser: Default ``BrowserOptions`` used when a call does not
            provide its own.

    Example:
        >>> flow = Flow(url="https://app.example.com", steps=[FlowStep()])
        >>> ai = ReadyAI(profiles={"qa": "/secure/qa-cookies.json"})
        >>> result = asyncio.run(ai.run_flow(flow, browser=BrowserOptions(profile="qa")))
        >>> result.status
        'passed'
    """

    def __init__(
        self,
        *,
        model: str = "gpt-4o-mini",
        output_dir: str = "./output",
        profiles: Optional[Mapping[str, Optional[Profile] | str]] = None,
        browser: Optional[BrowserOptions] = None,
    ):
        self.model = model
        self.output_dir = output_dir
        self._default_browser = browser or BrowserOptions()
        self._profiles: dict[str, Profile] = {}
        for name, value in (profiles or {}).items():
            if value is None:
                self._profiles[name] = Profile()
            elif isinstance(value, Profile):
                self._profiles[name] = value
            elif isinstance(value, str):
                self._profiles[name] = Profile(cookies_file=value)
            else:
                raise TypeError(
                    f"profile {name!r} must be None, a str cookies-file reference "
                    f"or a ready_ai.Profile, got {type(value).__name__}"
                )

    def _merge_browser(self, browser: Optional[BrowserOptions]) -> BrowserOptions:
        """Call-provided options override the defaults, field by field."""
        if browser is None:
            return self._default_browser
        merged = {
            **self._default_browser.model_dump(),
            **browser.model_dump(exclude_unset=True),
        }
        return BrowserOptions.model_validate(merged)

    def _resolve_profile(self, profile: Optional[str]) -> Profile:
        """Resolve a profile *name* to its runtime (reference-only) credentials."""
        if profile is None:
            return Profile()
        if profile not in self._profiles:
            registered = ", ".join(sorted(self._profiles)) or "none"
            raise UnknownProfileError(
                f"profile {profile!r} is not registered; "
                f"registered profiles: {registered}"
            )
        return self._profiles[profile]

    def validate_config(
        self, flow: Flow, *, browser: Optional[BrowserOptions] = None
    ) -> None:
        """Pre-flight a flow + browser configuration before running.

        Model-level constraints (URL, timeouts, effect policy, profile
        reference format) are enforced when the models are constructed;
        this additionally checks profile references against the registry.
        Raises ``ValidationError`` / ``UnknownProfileError`` on failure.
        """
        merged = self._merge_browser(browser)
        self._resolve_profile(merged.profile)
        return None

    async def run_flow(
        self,
        flow: Flow,
        *,
        browser: Optional[BrowserOptions] = None,
        confirm: Collection[str] | None = None,
    ) -> RunResult:
        """Execute a declarative flow and return a sanitized ``RunResult``.

        The flow runs through the engine's run-flow mode (no screenshots,
        no documentation rendering). ``flow.timeout_s`` caps the whole
        run; exceeding it raises ``RunTimeoutError``. Profile credentials
        are resolved from this instance's allowlist registry only.
        Pass ``confirm`` with the idempotency keys of steps declared with
        ``confirm=True`` to authorize their execution.
        """
        browser = self._merge_browser(browser)
        credentials = self._resolve_profile(browser.profile)
        output_dir = flow.output or self.output_dir
        run_id = flow.run_id or f"flow-{uuid.uuid4().hex[:8]}"

        flow_spec = _to_flow_spec(
            flow,
            run_id=run_id,
            headless=browser.headless,
            model=self.model,
            cookies_file=credentials.cookies_file,
            username=credentials.username,
            password=credentials.password,
        )
        loop = AgenticLoop(
            goal=flow.name or "run-flow",
            url=flow.url,
            model=self.model,
            output_dir=output_dir,
            port=browser.port,
            headless=browser.headless,
            cookies_file=credentials.cookies_file,
            username=credentials.username,
            password=credentials.password,
            profile_dir=credentials.user_data_dir,
            run_id=run_id,
        )
        try:
            if confirm is None:
                coro = loop.run_flow(flow_spec)
            else:
                coro = loop.run_flow(flow_spec, confirm=confirm)
            data = await asyncio.wait_for(coro, timeout=flow.timeout_s)
        except asyncio.TimeoutError as exc:
            raise RunTimeoutError(
                f"flow {flow.name or run_id!r} exceeded its "
                f"timeout_s={flow.timeout_s:g}s budget"
            ) from exc
        return RunResult.from_flow_result(data, output_dir=output_dir)

    async def replay_manifest(
        self,
        manifest: str | Path | Mapping,
        *,
        browser: Optional[BrowserOptions] = None,
        confirm: Collection[str] | None = None,
        fallback_agentic: bool = False,
        max_fallback_runs: int = 1,
    ) -> RunResult:
        """Replay a compiled manifest and return the observed result.

        ``manifest`` is a ``*_replay_manifest.json`` path or an already
        loaded manifest mapping. The replay runs the exact declared flow
        with ``allow_llm=False``: credential auto-login is refused, so
        replay relies on cookies or a persistent profile. Each step's live
        fingerprint is compared against the authoring-run fingerprint
        captured in the manifest (PH3B drift gate): a divergence reports
        the step with ``DRIFT_SUSPECTED`` instead of actuating silently.

        With ``fallback_agentic=True`` a drifted or failed replay escalates
        to the bounded agentic heal path — up to ``max_fallback_runs``
        re-runs with the LLM available. The result carries a ``replay``
        block (mode, first_status, drift, fallback) and, whenever the
        ``*_flow_metrics.json`` artifacts are present, a ``cost`` ledger
        (before/after, PH3C ``zero_token``). Pass ``confirm`` for steps the
        manifest declares with ``confirm=True``.
        """
        from src.agent.replay import read_manifest, run_replay

        loaded = (
            read_manifest(manifest)
            if isinstance(manifest, (str, Path))
            else manifest
        )
        browser = self._merge_browser(browser)
        credentials = self._resolve_profile(browser.profile)
        if credentials.username and credentials.password:
            raise ValueError(
                "replay cannot use credential auto-login (zero-LLM); use a "
                "persistent profile or cookies instead"
            )
        output_dir = self.output_dir
        run_id = f"replay-{uuid.uuid4().hex[:8]}"
        loop = AgenticLoop(
            goal="replay",
            url=loaded.get("url") or "",
            model=self.model,
            output_dir=output_dir,
            port=browser.port,
            headless=browser.headless,
            cookies_file=credentials.cookies_file,
            run_id=run_id,
        )
        try:
            data = await asyncio.wait_for(
                run_replay(
                    manifest=loaded,
                    loop=loop,
                    allow_fallback=fallback_agentic,
                    max_fallback_runs=max_fallback_runs,
                    confirm=confirm,
                ),
                timeout=300.0,
            )
        except asyncio.TimeoutError as exc:
            raise RunTimeoutError(
                f"replay {run_id!r} exceeded its 300.0s budget"
            ) from exc
        cost = self._replay_cost_ledger(
            loaded.get("source_run_id"), run_id, output_dir
        )
        return RunResult.from_flow_result(data, output_dir=output_dir, cost=cost)

    def _replay_cost_ledger(
        self,
        source_run_id: Optional[str],
        run_id: str,
        output_dir: str | Path,
    ) -> dict:
        """Best-effort before/after cost ledger (PH3C) from metrics artifacts.

        Reads ``{run_id}_flow_metrics.json`` (replay) and
        ``{source_run_id}_flow_metrics.json`` (authoring) from the output
        dir when they exist, then summarizes before/after via the SDK cost
        model. Missing artifacts degrade to zeros — never raise.
        """
        from ready_ai.replay import summarize_replay_cost

        def _read_metrics(rid: Optional[str]):
            if not rid:
                return None
            candidate = Path(output_dir) / f"{rid}_flow_metrics.json"
            if not candidate.is_file():
                return None
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None

        return summarize_replay_cost(
            _read_metrics(source_run_id), _read_metrics(run_id)
        )
