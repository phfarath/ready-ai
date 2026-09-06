"""Unit tests for the deterministic replay manifest (PH3A).

READY-AI-T-PH3A-REPLAY-MANIFEST: a verified (passed) flow compiles into a
versioned manifest, and replaying it runs with zero LLM involvement.
Mocked session throughout — no browser, no LLM.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.loop import AgenticLoop
from src.agent.replay import (
    REPLAY_MANIFEST_KIND,
    REPLAY_MANIFEST_VERSION,
    compile_manifest,
    manifest_to_flow_spec,
    read_manifest,
    write_manifest,
)
from src.api.models import FlowAction, FlowAssertion, FlowSpec, FlowStepSpec


def _flow(steps, *, retries=1):
    return FlowSpec(
        name="checkout",
        url="https://app.example.com/start",
        retries=retries,
        steps=steps,
    )


def _observe_flow():
    return _flow(
        [
            FlowStepSpec(
                name="Land",
                actions=[FlowAction(action="observe")],
                asserts=[
                    FlowAssertion(type="url_contains", expected="app.example.com")
                ],
            ),
            FlowStepSpec(
                name="Look",
                actions=[FlowAction(action="observe")],
            ),
        ]
    )


def _make_loop(tmp_path, run_id="replay-test", **kwargs):
    """AgenticLoop with a fully mocked BrowserSession."""
    loop = AgenticLoop(
        goal="replay",
        url="https://app.example.com/start",
        output_dir=str(tmp_path),
        run_id=run_id,
        headless=True,
        **kwargs,
    )
    session = loop._session
    session.setup = AsyncMock(return_value=None)
    session.teardown = AsyncMock(return_value=None)
    session.inject_cookies = AsyncMock(return_value=None)
    session.handle_login = AsyncMock(return_value=None)
    # Note: cookies_file/username/password stay as constructed (defaults
    # None) so credential-guard tests can pass them via kwargs.

    page = MagicMock()
    page.enable = AsyncMock(return_value=None)
    page.navigate = AsyncMock(return_value=None)
    page.wait_for_network_idle = AsyncMock(return_value=None)

    runtime = MagicMock()
    runtime.evaluate = AsyncMock(return_value="dom-state")
    runtime.query_selector = AsyncMock(return_value=None)
    runtime.get_element_text = AsyncMock(return_value="")
    runtime.get_visible_text = AsyncMock(return_value="")
    runtime.get_element_attributes = AsyncMock(return_value={})

    session._page = page
    session._runtime = runtime
    session._input = MagicMock()
    return loop, runtime


async def _passing_result(tmp_path, monkeypatch, run_id="replay-test"):
    async def dispatch(payload, *args, **kwargs):
        return "Observing current page state"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)
    loop, _runtime = _make_loop(tmp_path, run_id=run_id)
    # url_contains assert reads location.href via runtime.evaluate.
    loop._session._runtime.evaluate = AsyncMock(
        return_value="https://app.example.com/start"
    )
    result = await loop.run_flow(_observe_flow())
    assert result["status"] == "passed", result
    return result


async def test_run_flow_captures_pre_step_fingerprints(tmp_path, monkeypatch):
    result = await _passing_result(tmp_path, monkeypatch)
    fps = [s["fingerprint_pre"] for s in result["steps"]]
    assert len(fps) == 2
    assert all(isinstance(fp, str) and fp for fp in fps)


async def test_compile_manifest_from_passed_run(tmp_path, monkeypatch):
    result = await _passing_result(tmp_path, monkeypatch)
    manifest = compile_manifest(_observe_flow(), result)
    assert manifest["version"] == REPLAY_MANIFEST_VERSION
    assert manifest["kind"] == REPLAY_MANIFEST_KIND
    assert manifest["url"] == "https://app.example.com/start"
    assert manifest["source_run_id"] == "replay-test"
    assert len(manifest["steps"]) == 2
    first = manifest["steps"][0]
    assert first["index"] == 1
    assert first["actions"] == [{"action": "observe"}]
    assert first["asserts"] == [
        {"type": "url_contains", "expected": "app.example.com"}
    ]
    assert first["idempotency_key"] == "replay-test:step-1"
    assert first["fingerprint_pre"] == result["steps"][0]["fingerprint_pre"]


async def test_compile_refuses_failed_run(tmp_path, monkeypatch):
    async def dispatch(payload, *args, **kwargs):
        return "[Failed] nothing here"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)
    loop, _runtime = _make_loop(tmp_path)
    result = await loop.run_flow(_observe_flow())
    assert result["status"] == "failed"
    with pytest.raises(ValueError, match="only verified"):
        compile_manifest(_observe_flow(), result)


async def test_compile_refuses_credential_flow(tmp_path, monkeypatch):
    result = await _passing_result(tmp_path, monkeypatch)
    flow = _observe_flow()
    flow.username = "user@example.com"
    flow.password = "changeme"
    with pytest.raises(ValueError, match="credential auto-login"):
        compile_manifest(flow, result)


async def test_replay_round_trip_passes_with_zero_llm(tmp_path, monkeypatch):
    """Manifest → FlowSpec → run_flow(allow_llm=False) passes while any LLM
    construction explodes — the replay path never touches an LLM."""
    result = await _passing_result(tmp_path, monkeypatch)
    manifest = compile_manifest(_observe_flow(), result)
    path = write_manifest(manifest, tmp_path, "replay-test")
    assert path.is_file()
    loaded = read_manifest(path)
    assert loaded["version"] == REPLAY_MANIFEST_VERSION

    def _boom(*args, **kwargs):
        raise AssertionError("LLM must never be constructed during replay")

    monkeypatch.setattr("src.llm.client.LLMClient", _boom)
    replayed = manifest_to_flow_spec(loaded)
    loop, _runtime = _make_loop(tmp_path, run_id="replay-2")
    loop._session._runtime.evaluate = AsyncMock(
        return_value="https://app.example.com/start"
    )

    async def dispatch(payload, *args, **kwargs):
        return "Observing current page state"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)
    replay_result = await loop.run_flow(replayed, allow_llm=False)
    assert replay_result["status"] == "passed", replay_result


async def test_replay_with_credentials_fails_before_browser(tmp_path):
    loop, _runtime = _make_loop(
        tmp_path, username="user@example.com", password="changeme"
    )
    with pytest.raises(ValueError, match="credential auto-login"):
        await loop.run_flow(_observe_flow(), allow_llm=False)
    loop._session.setup.assert_not_awaited()


def test_manifest_to_flow_spec_rejects_bad_versions(tmp_path):
    with pytest.raises(ValueError, match="not a replay manifest"):
        manifest_to_flow_spec({"kind": "nope", "version": 1})
    with pytest.raises(ValueError, match="unsupported replay manifest version"):
        manifest_to_flow_spec({"kind": REPLAY_MANIFEST_KIND, "version": 999})
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"kind": REPLAY_MANIFEST_KIND}))
    with pytest.raises(ValueError, match="unsupported replay manifest version"):
        read_manifest(bad)


async def test_manifest_carries_no_secrets_of_its_own(tmp_path, monkeypatch):
    """Compile adds nothing sensitive: only flow-declared values land in the
    manifest — no cookies_file, no credentials (refused above)."""
    result = await _passing_result(tmp_path, monkeypatch)
    manifest = compile_manifest(_observe_flow(), result)
    blob = json.dumps(manifest)
    assert "cookies_file" not in blob
    assert "password" not in blob
    assert "username" not in blob


async def test_sdk_replay_manifest_runs_zero_llm(tmp_path, monkeypatch):
    from ready_ai import ReadyAI

    result = await _passing_result(tmp_path, monkeypatch, run_id="sdk-src")
    manifest = compile_manifest(_observe_flow(), result)
    path = write_manifest(manifest, tmp_path, "sdk-src")

    seen: dict = {}

    class FakeLoop:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        async def run_flow(self, flow_spec, **kwargs):
            seen["run_kwargs"] = kwargs
            return {
                "run_id": "replay-x",
                "flow": flow_spec.name,
                "url": flow_spec.url,
                "status": "passed",
                "steps": [],
                "summary": {},
            }

    monkeypatch.setattr("ready_ai.client.AgenticLoop", FakeLoop)
    ai = ReadyAI()
    out = await ai.replay_manifest(str(path))
    assert out.status == "passed"
    assert seen["run_kwargs"].get("allow_llm") is False
