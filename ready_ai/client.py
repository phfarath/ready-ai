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
import logging
import uuid
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
