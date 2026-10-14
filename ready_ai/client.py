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
- ``start()`` returns a ``RunHandle`` that streams ordered, sanitized
  ``RunEvent`` objects and supports cooperative cancellation.
- ``resume()`` continues a run paused at a human checkpoint from its
  ``run_id`` token alone — no engine internals required.
- Engine failures surface as the public exception taxonomy in
  ``ready_ai.exceptions`` (validation / policy / cancellation /
  timeout / connection) — never as internal ``src.*`` types.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import (
    Any,
    AsyncIterator,
    Collection,
    Mapping,
    Optional,
)

import websockets
from pydantic import ValidationError
from src.agent.state import RunState
from src.agent.loop import AgenticLoop
from src.cdp.exceptions import (
    CircuitOpenError,
    RunPolicyError,
    WebSocketDisconnected,
)
from src.observability import _redact

from .events import RunEvent
from .exceptions import (
    ConnectionLostError,
    FlowValidationError,
    PolicyViolationError,
    ReadyAIError,
    RunCancelledError,
    RunTimeoutError,
    UnknownProfileError,
)
from .models import BrowserOptions, EffectPolicy, Flow, Profile, RunResult

logger = logging.getLogger(__name__)

_QUEUE_END = object()
"""Sentinel pushed onto the event queue when the run task terminates."""

_EVENT_SCALAR_KEYS = frozenset(
    {"seq", "type", "run_id", "step_index", "step_name", "status"}
)


def _to_public_event(raw: Mapping[str, Any]) -> RunEvent:
    """Map an engine event dict onto a sanitized public ``RunEvent``.

    Every string crossing the boundary is scrubbed (URL userinfo plus
    embedded http(s) URLs reduced to scheme/host/path), so step names,
    failure reasons and checkpoint messages cannot leak credentials.
    """
    fields = {
        key: _redact(value, scrub_embedded=True) for key, value in raw.items()
    }
    detail = {
        key: value
        for key, value in fields.items()
        if key not in _EVENT_SCALAR_KEYS
    }
    return RunEvent(
        seq=int(fields.get("seq") or 0),
        type=str(fields.get("type") or "unknown"),
        run_id=str(fields.get("run_id") or ""),
        ts=time.time(),
        step_index=fields.get("step_index"),
        step_name=fields.get("step_name"),
        status=fields.get("status"),
        detail=detail,
    )


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


class RunHandle:
    """Handle to an in-flight run: stream events, await the result, cancel.

    Obtained from ``ReadyAI.start()``. The handle is bound to the
    asyncio loop that created it — call its methods from that loop.

    Cooperative cancellation (``cancel()``) asks the engine to stop at
    the next step/action boundary: the in-flight action finishes on its
    own budget, remaining steps report ``skipped`` and the run resolves
    with ``status="cancelled"``. For an immediate abort, cancel the task
    awaiting ``result()`` — that surfaces as ``RunCancelledError``.
    """

    def __init__(
        self,
        *,
        run_id: str,
        task: "asyncio.Task[RunResult]",
        queue: "asyncio.Queue",
        loop_obj: AgenticLoop,
    ):
        self.run_id = run_id
        self._task = task
        self._queue = queue
        self._loop_obj = loop_obj

    async def events(self) -> AsyncIterator[RunEvent]:
        """Yield ``RunEvent`` objects in ``seq`` order until the run ends.

        ``_QUEUE_END`` is always enqueued when the run task terminates —
        even on crash or cancellation — so the iterator can never hang.
        """
        while True:
            item = await self._queue.get()
            if item is _QUEUE_END:
                return
            yield item

    async def result(self) -> RunResult:
        """Await the run and return the sanitized ``RunResult``.

        Raises the public exception taxonomy on timeout, cancellation,
        policy refusal or connection failure.
        """
        return await self._task

    async def cancel(self) -> None:
        """Request cooperative cancellation (honored at step boundaries)."""
        self._loop_obj.request_cancel()

    @property
    def done(self) -> bool:
        """True once the underlying run task has completed."""
        return self._task.done()


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
        >>> result = asyncio.run(ai.run(flow, browser=BrowserOptions(profile="qa")))
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

    def _prepare(
        self,
        flow: Flow,
        browser: Optional[BrowserOptions],
        *,
        run_id: Optional[str] = None,
        resume_from: Optional[str] = None,
    ):
        """Build the (flow_spec, loop, output_dir, run_id) bundle for a run.

        Validation of public models happens at their construction;
        translating onto engine models is wrapped as
        ``FlowValidationError`` so callers see a public error type.
        """
        merged = self._merge_browser(browser)
        credentials = self._resolve_profile(merged.profile)
        output_dir = flow.output or self.output_dir
        rid = run_id or flow.run_id or f"flow-{uuid.uuid4().hex[:8]}"
        try:
            flow_spec = _to_flow_spec(
                flow,
                run_id=rid,
                headless=merged.headless,
                model=self.model,
                cookies_file=credentials.cookies_file,
                username=credentials.username,
                password=credentials.password,
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise FlowValidationError(
                f"flow {flow.name or rid!r} could not be translated onto "
                f"the engine model: {exc}"
            ) from exc
        loop = AgenticLoop(
            goal=flow.name or "run-flow",
            url=flow.url,
            model=self.model,
            output_dir=output_dir,
            port=merged.port,
            headless=merged.headless,
            cookies_file=credentials.cookies_file,
            username=credentials.username,
            password=credentials.password,
            profile_dir=credentials.user_data_dir,
            run_id=rid,
            resume_from=resume_from,
        )
        return flow_spec, loop, output_dir, rid

    async def _execute(
        self,
        loop: AgenticLoop,
        flow_spec,
        *,
        timeout_s: float,
        confirm: Optional[Collection[str]],
        output_dir: str,
        flow_label: str,
    ) -> RunResult:
        """Await ``loop.run_flow`` under the timeout and map every engine
        failure onto the public exception taxonomy."""
        try:
            if confirm is None:
                coro = loop.run_flow(flow_spec)
            else:
                coro = loop.run_flow(flow_spec, confirm=confirm)
            data = await asyncio.wait_for(coro, timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            raise RunTimeoutError(
                f"flow {flow_label!r} exceeded its "
                f"timeout_s={timeout_s:g}s budget"
            ) from exc
        except asyncio.CancelledError as exc:
            raise RunCancelledError(
                f"run {loop.run_id!r} was cancelled by the caller"
            ) from exc
        except RunPolicyError as exc:
            raise PolicyViolationError(str(exc)) from exc
        except (
            WebSocketDisconnected,
            CircuitOpenError,
            websockets.exceptions.ConnectionClosed,
        ) as exc:
            raise ConnectionLostError(
                f"run {loop.run_id!r} lost the browser connection: "
                f"{type(exc).__name__}"
            ) from exc
        return RunResult.from_flow_result(data, output_dir=output_dir)

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

    async def run(
        self,
        flow: Flow,
        *,
        browser: Optional[BrowserOptions] = None,
        confirm: Collection[str] | None = None,
    ) -> RunResult:
        """Execute a declarative flow and return a sanitized ``RunResult``.

        ``flow.timeout_s`` caps the whole run; exceeding it raises
        ``RunTimeoutError``. Profile credentials are resolved from this
        instance's allowlist registry only. Pass ``confirm`` with the
        idempotency keys of steps declared with ``confirm=True`` to
        authorize their execution. Cancelling the awaiting task raises
        ``RunCancelledError``.
        """
        flow_spec, loop, output_dir, rid = self._prepare(flow, browser)
        return await self._execute(
            loop,
            flow_spec,
            timeout_s=flow.timeout_s,
            confirm=confirm,
            output_dir=output_dir,
            flow_label=flow.name or rid,
        )

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
        return await self.run(flow, browser=browser, confirm=confirm)

    def run_sync(
        self,
        flow: Flow,
        *,
        browser: Optional[BrowserOptions] = None,
        confirm: Collection[str] | None = None,
    ) -> RunResult:
        """Synchronous adapter: runs ``run()`` on a fresh event loop.

        Must be called from a *synchronous* context only — when an event
        loop is already running it raises ``ReadyAIError`` instead of
        nesting a second loop. Inside async code, use
        ``await ai.run(...)`` (or ``ai.start(...)`` for observation).
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run(flow, browser=browser, confirm=confirm))
        raise ReadyAIError(
            "run_sync() cannot run inside an active asyncio loop; "
            "use `await ai.run(...)` instead"
        )

    def start(
        self,
        flow: Flow,
        *,
        browser: Optional[BrowserOptions] = None,
        confirm: Collection[str] | None = None,
    ) -> RunHandle:
        """Launch a run and return a ``RunHandle`` for observation/control.

        Must be called inside a running asyncio loop (the run is
        scheduled with ``asyncio.create_task``). The handle streams
        ordered ``RunEvent`` objects via ``events()``, resolves to the
        ``RunResult`` via ``result()``, and supports cooperative
        cancellation via ``cancel()``.
        """
        try:
            running = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise ReadyAIError(
                "start() requires a running asyncio loop; use "
                "`ai.run_sync(...)` from synchronous code"
            ) from exc
        flow_spec, loop, output_dir, rid = self._prepare(flow, browser)
        queue: asyncio.Queue = asyncio.Queue()
        loop.set_event_sink(lambda event: queue.put_nowait(_to_public_event(event)))

        async def _tracked() -> RunResult:
            try:
                return await self._execute(
                    loop,
                    flow_spec,
                    timeout_s=flow.timeout_s,
                    confirm=confirm,
                    output_dir=output_dir,
                    flow_label=flow.name or rid,
                )
            finally:
                queue.put_nowait(_QUEUE_END)

        task = running.create_task(_tracked())
        return RunHandle(run_id=rid, task=task, queue=queue, loop_obj=loop)

    async def resume(
        self,
        flow: Flow,
        *,
        run_id: str,
        browser: Optional[BrowserOptions] = None,
        confirm: Collection[str] | None = None,
    ) -> RunResult:
        """Resume a run paused at a human checkpoint, by ``run_id`` token.

        The checkpoint lives at ``{output_dir}/{run_id}_state.json``
        (``output_dir`` is the flow's ``output`` or the instance
        default). Fail-closed: a missing checkpoint, a checkpoint that
        is not ``PAUSED``, or a state whose ``run_id``/index does not
        match raises ``PolicyViolationError`` — the engine never
        silently re-executes a completed run.
        """
        output_dir = flow.output or self.output_dir
        state_path = Path(output_dir) / f"{run_id}_state.json"
        if not state_path.is_file():
            raise PolicyViolationError(
                f"no checkpoint found for run_id={run_id!r} under "
                f"{output_dir!r}; cannot resume"
            )
        state = RunState.from_file(state_path)
        status = getattr(state, "status", None) if state else None
        if status != "PAUSED":
            raise PolicyViolationError(
                f"checkpoint for run_id={run_id!r} is not paused "
                f"(status={status!r}); refusing to resume"
            )
        flow_spec, loop, out_dir, rid = self._prepare(
            flow, browser, run_id=run_id, resume_from=str(state_path)
        )
        return await self._execute(
            loop,
            flow_spec,
            timeout_s=flow.timeout_s,
            confirm=confirm,
            output_dir=out_dir,
            flow_label=flow.name or rid,
        )

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
            raise PolicyViolationError(
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
        except asyncio.CancelledError as exc:
            raise RunCancelledError(
                f"replay {run_id!r} was cancelled by the caller"
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
