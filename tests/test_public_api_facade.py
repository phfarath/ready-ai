"""ReadyAI façade wiring (READY-AI-T-13, DoD 2/3 integration).

The façade maps the public models onto the internal engine
(``src.agent.loop.AgenticLoop``) without leaking ``src.*`` to consumers.
Profiles are resolved from a registry of *references*; an unknown profile
reference is a validation error.
"""

from __future__ import annotations

import pytest

from ready_ai import (
    BrowserOptions,
    Flow,
    FlowAction,
    FlowStep,
    ReadyAI,
    RunResult,
    RunTimeoutError,
    UnknownProfileError,
)


def _flow(**overrides):
    kwargs = dict(
        name="checkout",
        url="https://app.example.com/start",
        timeout_s=30.0,
        steps=[
            FlowStep(
                name="Go to checkout",
                actions=[FlowAction(action="click", selector="#buy")],
                asserts=[],
                extract=[],
            )
        ],
    )
    kwargs.update(overrides)
    return Flow(**kwargs)


def _engine_result(run_id="flow-test", status="passed"):
    return {
        "run_id": run_id,
        "flow": "checkout",
        "url": "https://app.example.com/start",
        "status": status,
        "steps": [
            {
                "index": 1,
                "name": "Go to checkout",
                "actions": [
                    {
                        "action": "click",
                        "params": {"selector": "#buy"},
                        "description": "Clicked element: #buy",
                        "attempts": 1,
                        "passed": True,
                        "failure_reason": "",
                    }
                ],
                "asserts": [],
                "extracted": [],
                "attempts": 1,
                "status": "passed",
                "failure_reason": "",
                "skipped_asserts": 0,
                "skipped_extractions": 0,
            }
        ],
        "summary": {"steps_total": 1, "steps_passed": 1, "steps_failed": 0},
        "failure_reason": None,
    }


class _RecordingLoop:
    """Fake AgenticLoop that records constructor args and returns canned data."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.spec = None

    async def run_flow(self, flow_spec):
        self.spec = flow_spec
        return _engine_result(run_id=self.kwargs["run_id"])


class _LoopFactory:
    """Instantiates real _RecordingLoops and keeps the instances for asserts."""

    def __init__(self):
        self.instances = []

    def __call__(self, **kwargs):
        instance = _RecordingLoop(**kwargs)
        self.instances.append(instance)
        return instance


def _patch_loop(monkeypatch) -> _LoopFactory:
    factory = _LoopFactory()
    monkeypatch.setattr("ready_ai.client.AgenticLoop", factory)
    return factory


@pytest.mark.asyncio
async def test_run_flow_maps_flow_and_browser_onto_engine(tmp_path, monkeypatch):
    factory = _patch_loop(monkeypatch)

    ai = ReadyAI(
        output_dir=str(tmp_path),
        model="claude-sonnet-4-20250514",
        profiles={"alice": "/tmp/cookies-alice.json"},
    )
    flow = _flow(run_id="flow-test")
    browser = BrowserOptions(headless=True, port=9333, profile="alice")

    result = await ai.run_flow(flow, browser=browser)
    fake = factory.instances[0]

    assert isinstance(result, RunResult)
    assert result.status == "passed"
    assert result.run_id == "flow-test"

    # The engine was constructed with the resolved profile reference (cookies
    # file path) and browser context — never with cookies bytes.
    assert fake.kwargs["cookies_file"] == "/tmp/cookies-alice.json"
    assert fake.kwargs["headless"] is True
    assert fake.kwargs["port"] == 9333
    assert fake.kwargs["output_dir"] == str(tmp_path)
    assert fake.kwargs["model"] == "claude-sonnet-4-20250514"
    assert fake.kwargs["goal"] == "checkout"

    # The declarative flow was translated onto the engine's FlowSpec.
    spec = fake.spec
    assert spec.url == "https://app.example.com/start"
    assert spec.headless is True
    assert spec.cookies_file == "/tmp/cookies-alice.json"
    assert spec.steps[0].actions[0].action == "click"


@pytest.mark.asyncio
async def test_run_flow_defaults_browser_and_no_profile(tmp_path, monkeypatch):
    factory = _patch_loop(monkeypatch)

    ai = ReadyAI(output_dir=str(tmp_path))
    result = await ai.run_flow(_flow())
    fake = factory.instances[0]

    assert result.status == "passed"
    assert fake.kwargs["cookies_file"] is None
    assert fake.kwargs["username"] is None
    assert fake.kwargs["password"] is None
    assert fake.kwargs["headless"] is True
    assert fake.kwargs["port"] == 9222


@pytest.mark.asyncio
async def test_profile_string_is_cookies_file_reference(tmp_path, monkeypatch):
    factory = _patch_loop(monkeypatch)

    ai = ReadyAI(output_dir=str(tmp_path), profiles={"bob": "/tmp/bob.json"})
    await ai.run_flow(_flow(), browser=BrowserOptions(profile="bob"))
    fake = factory.instances[0]

    assert fake.kwargs["cookies_file"] == "/tmp/bob.json"


@pytest.mark.asyncio
async def test_unknown_profile_reference_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("ready_ai.client.AgenticLoop", lambda **kw: object())
    ai = ReadyAI(output_dir=str(tmp_path), profiles={"alice": None})

    with pytest.raises(UnknownProfileError, match="alice"):
        await ai.run_flow(_flow(), browser=BrowserOptions(profile="ghost"))


@pytest.mark.asyncio
async def test_flow_override_run_id_and_output(tmp_path, monkeypatch):
    factory = _patch_loop(monkeypatch)

    custom_out = tmp_path / "custom"
    ai = ReadyAI(output_dir=str(tmp_path))
    await ai.run_flow(
        _flow(run_id="my-run-1", output=str(custom_out)),
        browser=BrowserOptions(),
    )
    fake = factory.instances[0]

    assert fake.spec.run_id == "my-run-1"
    assert fake.kwargs["output_dir"] == str(custom_out)


@pytest.mark.asyncio
async def test_run_timeout_raises_public_runtimeout(tmp_path, monkeypatch):
    class _SlowLoop:
        async def run_flow(self, flow_spec):  # pragma: no cover - sleeps forever
            import asyncio

            await asyncio.sleep(30)

    monkeypatch.setattr("ready_ai.client.AgenticLoop", lambda **kw: _SlowLoop())

    ai = ReadyAI(output_dir=str(tmp_path))
    flow = _flow(timeout_s=0.01)

    with pytest.raises(RunTimeoutError, match="timeout_s"):
        await ai.run_flow(flow)


def test_validate_config_preflight(tmp_path):
    ai = ReadyAI(output_dir=str(tmp_path), profiles={"alice": None})

    # Valid configuration passes without raising.
    assert (
        ai.validate_config(
            _flow(), browser=BrowserOptions(headless=True, profile="alice")
        )
        is None
    )

    with pytest.raises(UnknownProfileError):
        ai.validate_config(_flow(), browser=BrowserOptions(profile="ghost"))


def test_invalid_registry_entry_rejected(tmp_path):
    with pytest.raises(TypeError):
        ReadyAI(output_dir=str(tmp_path), profiles={"bad": 123})


def test_ready_ai_public_docstring(tmp_path):
    import ready_ai

    assert "ReadyAI" in (ready_ai.ReadyAI.__doc__ or "")
    assert "RunResult" in (ready_ai.ReadyAI.run_flow.__doc__ or "")
    assert "timeout" in (ready_ai.ReadyAI.run_flow.__doc__ or "")
