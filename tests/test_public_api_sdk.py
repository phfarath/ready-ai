"""Public async-first SDK surface (READY-AI-T-14).

The façade exposes async ``run()``/``start()``, an explicit ``run_sync()``
adapter, cooperative cancellation, an ordered/sanitized event stream,
checkpoint resume by ``run_id`` token, and a public exception taxonomy
(validation / policy / cancellation / timeout / connection) — no
``src.*`` types cross the boundary.
"""

from __future__ import annotations

import asyncio

import pytest

import ready_ai
from ready_ai import (
    ConnectionLostError,
    Flow,
    FlowAction,
    FlowStep,
    FlowValidationError,
    PolicyViolationError,
    ReadyAI,
    ReadyAIError,
    RunCancelledError,
    RunEvent,
    RunResult,
    RunTimeoutError,
)
from src.agent.state import RunState
from src.cdp.exceptions import (
    CircuitOpenError,
    RunPolicyError,
    WebSocketDisconnected,
)


def _flow(**overrides):
    kwargs = dict(
        name="sdk-flow",
        url="https://app.example.com/start",
        timeout_s=30.0,
        steps=[
            FlowStep(
                name="Go",
                actions=[
                    FlowAction(
                        action="navigate", url="https://app.example.com/done"
                    )
                ],
            )
        ],
    )
    kwargs.update(overrides)
    return Flow(**kwargs)


def _engine_result(run_id, status="passed"):
    return {
        "run_id": run_id,
        "flow": "sdk-flow",
        "url": "https://app.example.com/start",
        "status": status,
        "steps": [
            {
                "index": 1,
                "name": "Go",
                "actions": [],
                "asserts": [],
                "extracted": [],
                "attempts": 1,
                "status": "passed",
                "failure_reason": "",
                "skipped_asserts": 0,
                "skipped_extractions": 0,
            }
        ],
        "summary": {"steps_total": 1, "steps_passed": 1},
        "failure_reason": None,
    }


class _ScriptedLoop:
    """Fake AgenticLoop: records kwargs, replays canned events/results."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.run_id = kwargs.get("run_id", "run-x")
        self.resume_from = kwargs.get("resume_from")
        self.spec = None
        self.confirm = None
        self._sink = None
        self._cancelled = False
        self.events = []
        self.result_payload = None

    def set_event_sink(self, sink):
        self._sink = sink

    def request_cancel(self):
        self._cancelled = True

    def _emit(self, **event):
        if self._sink:
            self._sink({"seq": 0, "run_id": self.run_id, **event})

    async def run_flow(self, flow_spec, confirm=None):
        self.spec = flow_spec
        self.confirm = confirm
        for i, event in enumerate(self.events, start=1):
            event.setdefault("seq", i)
            self._sink(dict(event, run_id=self.run_id))
            await asyncio.sleep(0)
        return self.result_payload or _engine_result(self.run_id)


class _LoopFactory:
    def __init__(self, *, events=None, result=None):
        self.instances = []
        self.events = events or []
        self.result = result

    def __call__(self, **kwargs):
        instance = _ScriptedLoop(**kwargs)
        instance.events = list(self.events)
        instance.result_payload = self.result
        self.instances.append(instance)
        return instance


def _patch_loop(monkeypatch, **kwargs) -> _LoopFactory:
    factory = _LoopFactory(**kwargs)
    monkeypatch.setattr("ready_ai.client.AgenticLoop", factory)
    return factory


# ─── Async execution ─────────────────────────────────────────────────


async def test_run_inside_existing_asyncio_loop(tmp_path, monkeypatch):
    """The primary path: ``await ai.run(...)`` inside a caller-owned loop."""
    assert asyncio.get_running_loop() is not None
    factory = _patch_loop(monkeypatch)

    ai = ReadyAI(output_dir=str(tmp_path))
    result = await ai.run(_flow(run_id="r1"))

    assert isinstance(result, RunResult)
    assert result.status == "passed"
    assert factory.instances[0].run_id == "r1"


async def test_run_flow_alias_kept(tmp_path, monkeypatch):
    _patch_loop(monkeypatch)
    ai = ReadyAI(output_dir=str(tmp_path))
    result = await ai.run_flow(_flow())
    assert result.status == "passed"


def test_run_sync_runs_outside_a_loop(tmp_path, monkeypatch):
    _patch_loop(monkeypatch)
    ai = ReadyAI(output_dir=str(tmp_path))
    result = ai.run_sync(_flow())
    assert isinstance(result, RunResult)
    assert result.status == "passed"


async def test_run_sync_inside_a_loop_fails_explicitly(tmp_path, monkeypatch):
    """No nested event loop — sync adapter refuses with a clear error."""
    _patch_loop(monkeypatch)
    ai = ReadyAI(output_dir=str(tmp_path))
    with pytest.raises(ReadyAIError, match="active asyncio loop"):
        ai.run_sync(_flow())


def test_start_outside_a_loop_fails_explicitly(tmp_path):
    ai = ReadyAI(output_dir=str(tmp_path))
    with pytest.raises(ReadyAIError, match="running asyncio loop"):
        ai.start(_flow())


# ─── Exception taxonomy ──────────────────────────────────────────────


async def test_timeout_maps_to_run_timeout_error(tmp_path, monkeypatch):
    class _Slow:
        def __init__(self, **kw):
            self.run_id = kw.get("run_id")

        def set_event_sink(self, sink):
            pass

        async def run_flow(self, spec, confirm=None):
            await asyncio.sleep(30)

    monkeypatch.setattr("ready_ai.client.AgenticLoop", _Slow)
    ai = ReadyAI(output_dir=str(tmp_path))
    with pytest.raises(RunTimeoutError, match="timeout_s"):
        await ai.run(_flow(timeout_s=0.01))


async def test_task_cancellation_maps_to_run_cancelled(tmp_path, monkeypatch):
    class _Blocking:
        def __init__(self, **kw):
            self.run_id = kw.get("run_id")

        async def run_flow(self, spec, confirm=None):
            await asyncio.Event().wait()

    monkeypatch.setattr("ready_ai.client.AgenticLoop", _Blocking)
    ai = ReadyAI(output_dir=str(tmp_path))
    task = asyncio.create_task(ai.run(_flow()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(RunCancelledError):
        await task


@pytest.mark.parametrize(
    "exc",
    [
        WebSocketDisconnected("socket gone"),
        CircuitOpenError("circuit open", state="down", attempts=3),
    ],
)
async def test_connection_failures_map_to_connection_lost(
    tmp_path, monkeypatch, exc
):
    class _Dying:
        def __init__(self, **kw):
            self.run_id = kw.get("run_id")

        async def run_flow(self, spec, confirm=None):
            raise exc

    monkeypatch.setattr("ready_ai.client.AgenticLoop", _Dying)
    ai = ReadyAI(output_dir=str(tmp_path))
    with pytest.raises(ConnectionLostError):
        await ai.run(_flow())


async def test_engine_internal_timeout_maps_connection_lost(
    tmp_path, monkeypatch
):
    """A builtin TimeoutError inside the engine (CDP command timeout)
    is NOT the run budget — it must not alias to RunTimeoutError."""

    class _TimingOut:
        def __init__(self, **kw):
            self.run_id = kw.get("run_id")

        async def run_flow(self, spec, confirm=None):
            raise TimeoutError("CDP command Page.navigate timed out")

    monkeypatch.setattr("ready_ai.client.AgenticLoop", _TimingOut)
    ai = ReadyAI(output_dir=str(tmp_path))
    with pytest.raises(ConnectionLostError, match="internal timeout"):
        await ai.run(_flow())


async def test_generic_engine_error_maps_to_readyai_error(
    tmp_path, monkeypatch
):
    class _Broken:
        def __init__(self, **kw):
            self.run_id = kw.get("run_id")

        async def run_flow(self, spec, confirm=None):
            raise RuntimeError("protocol blew up")

    monkeypatch.setattr("ready_ai.client.AgenticLoop", _Broken)
    ai = ReadyAI(output_dir=str(tmp_path))
    with pytest.raises(ReadyAIError, match="RuntimeError"):
        await ai.run(_flow())


async def test_policy_refusal_maps_to_policy_violation(tmp_path, monkeypatch):
    class _Refusing:
        def __init__(self, **kw):
            self.run_id = kw.get("run_id")

        async def run_flow(self, spec, confirm=None):
            raise RunPolicyError("paused checkpoint run_id mismatch")

    monkeypatch.setattr("ready_ai.client.AgenticLoop", _Refusing)
    ai = ReadyAI(output_dir=str(tmp_path))
    with pytest.raises(PolicyViolationError, match="checkpoint"):
        await ai.run(_flow())


async def test_translation_error_maps_to_flow_validation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "ready_ai.client._to_flow_spec",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad step")),
    )
    ai = ReadyAI(output_dir=str(tmp_path))
    with pytest.raises(FlowValidationError, match="translated"):
        await ai.run(_flow())


# ─── Events stream ───────────────────────────────────────────────────


async def test_events_stream_order_schema_and_terminal_event(
    tmp_path, monkeypatch
):
    _patch_loop(
        monkeypatch,
        events=[
            {"type": "run_started", "flow": "sdk-flow", "steps": 1},
            {"type": "step_started", "step_index": 1, "step_name": "Go"},
            {"type": "retry", "action": "navigate", "attempt": 2},
            {
                "type": "step_finished",
                "step_index": 1,
                "step_name": "Go",
                "status": "passed",
            },
            {"type": "run_finished", "status": "passed"},
        ],
    )
    ai = ReadyAI(output_dir=str(tmp_path))
    handle = ai.start(_flow(run_id="ev-1"))

    collected = [event async for event in handle.events()]
    result = await handle.result()

    assert result.status == "passed"
    assert all(isinstance(e, RunEvent) for e in collected)
    types = [e.type for e in collected]
    assert types == [
        "run_started",
        "step_started",
        "retry",
        "step_finished",
        "run_finished",
    ]
    assert collected[0].seq < collected[-1].seq
    assert collected[-1].status == "passed"
    retry = collected[2]
    assert retry.detail["action"] == "navigate"
    assert retry.detail["attempt"] == 2
    assert collected[1].step_index == 1


async def test_events_terminate_when_the_run_raises(tmp_path, monkeypatch):
    class _Dying:
        def __init__(self, **kw):
            self.run_id = kw.get("run_id")
            self._sink = None

        def set_event_sink(self, sink):
            self._sink = sink

        async def run_flow(self, spec, confirm=None):
            self._sink({"seq": 1, "type": "run_started", "run_id": self.run_id})
            raise WebSocketDisconnected("gone")

    monkeypatch.setattr("ready_ai.client.AgenticLoop", _Dying)
    ai = ReadyAI(output_dir=str(tmp_path))
    handle = ai.start(_flow())

    collected = [event async for event in handle.events()]
    # The SDK synthesizes the terminal run_finished even when the engine
    # raised — the stream always ends with an explicit outcome.
    assert [e.type for e in collected] == ["run_started", "run_finished"]
    assert collected[-1].status == "error"
    with pytest.raises(ConnectionLostError):
        await handle.result()


async def test_no_duplicate_terminal_event_when_engine_emitted(
    tmp_path, monkeypatch
):
    """The engine emits run_finished(error) before re-raising — the SDK
    synthesis must not append a second terminal event."""

    class _TerminalDying:
        def __init__(self, **kw):
            self.run_id = kw.get("run_id")
            self._sink = None

        def set_event_sink(self, sink):
            self._sink = sink

        async def run_flow(self, spec, confirm=None):
            self._sink({"seq": 1, "type": "run_started", "run_id": self.run_id})
            self._sink(
                {
                    "seq": 2,
                    "type": "run_finished",
                    "run_id": self.run_id,
                    "status": "error",
                    "failure_reason": "boom",
                }
            )
            raise WebSocketDisconnected("gone")

    monkeypatch.setattr("ready_ai.client.AgenticLoop", _TerminalDying)
    ai = ReadyAI(output_dir=str(tmp_path))
    handle = ai.start(_flow())

    collected = [event async for event in handle.events()]
    assert [e.type for e in collected].count("run_finished") == 1
    with pytest.raises(ConnectionLostError):
        await handle.result()


async def test_event_payloads_are_scrubbed(tmp_path, monkeypatch):
    _patch_loop(
        monkeypatch,
        events=[
            {"type": "run_started"},
            {
                "type": "step_finished",
                "step_index": 1,
                "status": "failed",
                "failure_reason": "expected url https://user:pass@app.example.com/in?token=abc to load",
            },
            {"type": "run_finished", "status": "failed"},
        ],
    )
    ai = ReadyAI(output_dir=str(tmp_path))
    handle = ai.start(_flow())
    collected = [event async for event in handle.events()]
    await handle.result()

    failed = collected[1]
    payload = repr(failed.detail) + (failed.step_name or "")
    assert "user:pass@" not in payload
    assert "token=abc" not in payload


# ─── Cooperative cancellation ────────────────────────────────────────


async def test_cooperative_cancel_via_handle(tmp_path, monkeypatch):
    """handle.cancel() flips the engine flag; the run resolves cancelled."""

    class _Cancellable(_ScriptedLoop):
        async def run_flow(self, spec, confirm=None):
            self._emit(type="run_started", seq=1)
            self._emit(type="step_started", seq=2, step_index=1)
            # The engine checks the flag at the next boundary.
            while not self._cancelled:
                await asyncio.sleep(0.005)
            self._emit(
                type="run_finished", seq=3, status="cancelled"
            )
            return _engine_result(self.run_id, status="cancelled")

    monkeypatch.setattr("ready_ai.client.AgenticLoop", _Cancellable)
    ai = ReadyAI(output_dir=str(tmp_path))
    handle = ai.start(_flow())

    events = []
    async for event in handle.events():
        events.append(event)
        if event.type == "step_started":
            await handle.cancel()

    result = await handle.result()
    assert result.status == "cancelled"
    assert events[-1].status == "cancelled"


async def test_task_cancel_synthesizes_run_finished(tmp_path, monkeypatch):
    """Cancelling the awaiting task still yields a terminal event."""

    class _Blocking:
        def __init__(self, **kw):
            self.run_id = kw.get("run_id")
            self._sink = None

        def set_event_sink(self, sink):
            self._sink = sink

        async def run_flow(self, spec, confirm=None):
            self._sink({"seq": 1, "type": "run_started", "run_id": self.run_id})
            await asyncio.Event().wait()

    monkeypatch.setattr("ready_ai.client.AgenticLoop", _Blocking)
    ai = ReadyAI(output_dir=str(tmp_path))
    handle = ai.start(_flow())

    events = []
    result_task = asyncio.create_task(handle.result())
    async for event in handle.events():
        events.append(event)
        if event.type == "run_started":
            result_task.cancel()

    with pytest.raises(RunCancelledError):
        await result_task
    assert events[-1].type == "run_finished"
    assert events[-1].status == "cancelled"


# ─── Checkpoint resume ───────────────────────────────────────────────


def _write_checkpoint(output_dir, run_id, status="PAUSED", step_index=2):
    state = RunState(
        run_id=run_id,
        goal="sdk-flow",
        url="https://app.example.com/start",
        status=status,
        current_step_index=step_index,
        pause_reason="Finish SSO",
        resume_when={"selector_visible": "#authed"},
    )
    state.to_file(output_dir / f"{run_id}_state.json")


def _write_fingerprinted_checkpoint(output_dir, flow, run_id, step_index=1):
    """Checkpoint bound to ``flow`` via the engine fingerprint helper."""
    from src.agent.state import compute_flow_fingerprint
    from src.api.models import (
        FlowAction as _FlowAction,
        FlowSpec as _FlowSpec,
        FlowStepSpec as _FlowStepSpec,
    )

    spec = _FlowSpec(
        name=flow.name,
        url=flow.url,
        steps=[
            _FlowStepSpec(
                name=step.name,
                actions=[
                    _FlowAction(**a.model_dump(exclude_none=True))
                    for a in step.actions
                ],
                asserts=[],
                extract=[],
            )
            for step in flow.steps
        ],
    )
    state = RunState(
        run_id=run_id,
        goal=flow.name or "sdk-flow",
        url=flow.url,
        status="PAUSED",
        current_step_index=step_index,
        pause_reason="Finish SSO",
        resume_when={"selector_visible": "#authed"},
        flow_fingerprint=compute_flow_fingerprint(spec),
    )
    state.to_file(output_dir / f"{run_id}_state.json")


@pytest.mark.parametrize("bad", ["../escape", "..\\win", "a b", "a/b", ""])
async def test_resume_rejects_traversal_run_id(tmp_path, bad):
    """The run token reaches a filesystem path — validate its charset."""
    ai = ReadyAI(output_dir=str(tmp_path))
    with pytest.raises(FlowValidationError, match="run token"):
        await ai.resume(_flow(), run_id=bad)


async def test_resume_without_checkpoint_refuses(tmp_path):
    ai = ReadyAI(output_dir=str(tmp_path))
    with pytest.raises(PolicyViolationError, match="no checkpoint"):
        await ai.resume(_flow(), run_id="ghost-run")


async def test_resume_non_paused_checkpoint_refuses(tmp_path):
    _write_checkpoint(tmp_path, "done-run", status="FINISHED")
    ai = ReadyAI(output_dir=str(tmp_path))
    with pytest.raises(PolicyViolationError, match="not paused"):
        await ai.resume(_flow(), run_id="done-run")


async def test_resume_uses_run_id_token_only(tmp_path, monkeypatch):
    """The consumer resumes with just run_id — no engine internals."""
    _write_checkpoint(tmp_path, "sso-1", status="PAUSED", step_index=2)
    factory = _patch_loop(monkeypatch)
    ai = ReadyAI(output_dir=str(tmp_path))

    result = await ai.resume(_flow(), run_id="sso-1")

    assert result.status == "passed"
    loop = factory.instances[0]
    assert loop.kwargs["run_id"] == "sso-1"
    assert loop.kwargs["resume_from"].endswith("sso-1_state.json")


async def test_resume_refuses_foreign_flow_fingerprint(tmp_path, monkeypatch):
    """A checkpoint bound to flow A refuses flow B at the SDK boundary."""
    origin = _flow(run_id="sso-2")
    _write_fingerprinted_checkpoint(tmp_path, origin, "sso-2")
    _patch_loop(monkeypatch)
    ai = ReadyAI(output_dir=str(tmp_path))

    foreign = _flow(
        run_id="sso-2",
        steps=[
            FlowStep(
                name="Other",
                actions=[
                    FlowAction(
                        action="navigate",
                        url="https://app.example.com/other",
                    )
                ],
            )
        ],
    )
    with pytest.raises(PolicyViolationError, match="different flow"):
        await ai.resume(foreign, run_id="sso-2")


async def test_resume_accepts_matching_flow_fingerprint(tmp_path, monkeypatch):
    """The originating flow resumes past the SDK boundary check."""
    origin = _flow(run_id="sso-3")
    _write_fingerprinted_checkpoint(tmp_path, origin, "sso-3")
    factory = _patch_loop(monkeypatch)
    ai = ReadyAI(output_dir=str(tmp_path))

    result = await ai.resume(_flow(run_id="sso-3"), run_id="sso-3")

    assert result.status == "passed"
    assert factory.instances[0].kwargs["run_id"] == "sso-3"


# ─── Public surface ──────────────────────────────────────────────────


def test_new_names_exported():
    for name in (
        "RunHandle",
        "RunEvent",
        "EVENT_TYPES",
        "RunCancelledError",
        "ConnectionLostError",
        "PolicyViolationError",
        "FlowValidationError",
    ):
        assert hasattr(ready_ai, name)
        assert name in ready_ai.__all__


def test_exception_taxonomy_hierarchy():
    assert issubclass(ready_ai.RunTimeoutError, ReadyAIError)
    assert issubclass(ready_ai.RunCancelledError, ReadyAIError)
    assert issubclass(ready_ai.ConnectionLostError, ReadyAIError)
    assert issubclass(ready_ai.PolicyViolationError, ReadyAIError)
    assert issubclass(ready_ai.FlowValidationError, ValueError)


def test_result_failure_reason_is_scrubbed():
    result = RunResult.from_flow_result(
        {
            "run_id": "r1",
            "status": "failed",
            "failure_reason": (
                "expected https://user:pass@app.example.com/x?token=abc"
            ),
            "steps": [],
            "summary": {},
        },
        output_dir=".",
    )
    assert "user:pass@" not in (result.failure_reason or "")
    assert "token=abc" not in (result.failure_reason or "")


async def test_step_level_diag_text_is_scrubbed():
    """Assert diagnostics embed the live page URL — query tokens and
    userinfo must not survive into RunResult step payloads."""
    result = RunResult.from_flow_result(
        {
            "run_id": "r1",
            "status": "failed",
            "steps": [
                {
                    "index": 1,
                    "status": "failed",
                    "failure_reason": "see https://app.example.com/cb?code=sekrit",
                    "actions": [
                        {
                            "action": "click",
                            "status": "failed",
                            "description": "err at https://u:p@h.io/x?t=1",
                            "failure_reason": "boom https://h.io?token=zz",
                        }
                    ],
                    "asserts": [
                        {
                            "type": "url_contains",
                            "status": "failed",
                            "message": "expected x actual https://app.io/cb?session=abc",
                            "actual": "https://app.io/cb?session=abc",
                        }
                    ],
                    "extracted": [
                        {"name": "link", "value": "https://site.io/a?keep=1"}
                    ],
                }
            ],
            "summary": {},
        },
        output_dir=".",
    )
    step = result.steps[0]
    assert "code=sekrit" not in step.failure_reason
    assert "app.example.com/cb" in step.failure_reason  # path kept
    action = step.actions[0]
    assert "u:p@" not in action["description"]
    assert "token=zz" not in action["failure_reason"]
    assertion = step.asserts[0]
    assert "session=abc" not in assertion["message"]
    assert "session=abc" not in assertion["actual"]
    # Extraction payloads are caller-requested data, not diagnostics.
    assert step.extracted[0]["value"] == "https://site.io/a?keep=1"


async def test_replay_manifest_load_errors_map_to_flow_validation(tmp_path):
    """Missing/corrupt manifest paths and non-mapping manifests must not
    leak raw builtins across the public boundary."""
    ai = ReadyAI(output_dir=str(tmp_path))
    with pytest.raises(FlowValidationError, match="cannot read"):
        await ai.replay_manifest(tmp_path / "nope_replay_manifest.json")
    with pytest.raises(FlowValidationError, match="path or a mapping"):
        await ai.replay_manifest(42)


async def test_replay_manifest_invalid_mapping_maps_to_flow_validation(
    tmp_path,
):
    """A malformed Mapping manifest is rejected at the SDK boundary —
    not as a generic ReadyAIError from deep inside the replay path."""
    ai = ReadyAI(output_dir=str(tmp_path))
    with pytest.raises(FlowValidationError, match="invalid replay manifest"):
        await ai.replay_manifest({"kind": "not-a-manifest"})


async def test_replay_manifest_recursion_error_maps_to_flow_validation(
    tmp_path, monkeypatch
):
    """A >1000-deep manifest mapping must not leak a raw RecursionError."""
    def _boom(manifest):
        raise RecursionError("maximum recursion depth exceeded")

    # replay_manifest imports the constructor locally from src.agent.replay.
    monkeypatch.setattr("src.agent.replay.manifest_to_flow_spec", _boom)
    ai = ReadyAI(output_dir=str(tmp_path))
    with pytest.raises(FlowValidationError, match="invalid replay manifest"):
        await ai.replay_manifest({"kind": "ready-ai-replay-manifest"})


async def test_result_conversion_type_errors_map_to_flow_validation(
    tmp_path, monkeypatch
):
    """A non-mapping step entry raises TypeError inside RunStep(**step) —
    must surface as FlowValidationError, not a raw builtin."""
    _patch_loop(
        monkeypatch,
        result={
            "run_id": "r1",
            "status": "passed",
            "steps": ["not-a-dict"],
            "summary": {},
        },
    )
    ai = ReadyAI(output_dir=str(tmp_path))
    with pytest.raises(FlowValidationError, match="malformed"):
        await ai.run(_flow())
