"""Drift gate + bounded agentic fallback (PH3B).

READY-AI-T-PH3B-DRIFT-FALLBACK: replay compares each step's live
fingerprint against the manifest BEFORE acting; divergence (or any failed
step) triggers at most one trust-live agentic re-run, and the divergence
stays observable (DRIFT_SUSPECTED) — never a silent heal.
Mocked session throughout — no browser, no LLM.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.agent.loop import AgenticLoop
from src.agent.replay import (
    DRIFT_SUSPECTED,
    compile_manifest,
    run_replay,
)
from src.api.models import FlowAction, FlowAssertion, FlowSpec, FlowStepSpec


def _flow():
    return FlowSpec(
        name="checkout",
        url="https://app.example.com/start",
        steps=[
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
        ],
    )


def _make_loop(tmp_path, run_id="drift-test", **kwargs):
    """AgenticLoop with a fully mocked BrowserSession."""
    loop = AgenticLoop(
        goal="drift",
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

    page = MagicMock()
    page.enable = AsyncMock(return_value=None)
    page.navigate = AsyncMock(return_value=None)
    page.wait_for_network_idle = AsyncMock(return_value=None)

    runtime = MagicMock()
    runtime.evaluate = AsyncMock(return_value="https://app.example.com/start")
    runtime.query_selector = AsyncMock(return_value=None)
    runtime.get_element_text = AsyncMock(return_value="")
    runtime.get_visible_text = AsyncMock(return_value="")
    runtime.get_element_attributes = AsyncMock(return_value={})

    session._page = page
    session._runtime = runtime
    session._input = MagicMock()
    return loop


async def _author(tmp_path, monkeypatch, run_id="drift-author"):
    async def dispatch(payload, *args, **kwargs):
        return "Observing current page state"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)
    loop = _make_loop(tmp_path, run_id=run_id)
    result = await loop.run_flow(_flow())
    assert result["status"] == "passed", result
    return compile_manifest(_flow(), result)


async def test_gate_blocks_actuation_on_drift(tmp_path, monkeypatch):
    """Tampered step-2 expectation: step 2 never dispatches, reports drift."""
    manifest = await _author(tmp_path, monkeypatch)
    calls: list = []

    async def dispatch(payload, *args, **kwargs):
        calls.append(payload)
        return "Observing current page state"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)
    loop = _make_loop(tmp_path, run_id="drift-replay")
    expected = [manifest["steps"][0]["fingerprint_pre"], "tampered-fingerprint"]
    result = await loop.run_flow(
        _flow(), allow_llm=False, expected_fingerprints=expected
    )
    assert result["status"] == "failed", result
    assert len(calls) == 1  # step 1 acted; step 2 was gated before acting
    step2 = result["steps"][1]
    assert step2["status"] == "failed"
    assert step2["actions"] == []
    assert step2["drift"]["signal"] == DRIFT_SUSPECTED
    assert step2["drift"]["expected"] == "tampered-fingerprint"

    from src.observability import get_metrics

    assert get_metrics().get_counter("replay.drift_suspected") >= 1


async def test_matching_fingerprints_pass_silently(tmp_path, monkeypatch):
    manifest = await _author(tmp_path, monkeypatch)

    async def dispatch(payload, *args, **kwargs):
        return "Observing current page state"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)
    loop = _make_loop(tmp_path, run_id="drift-clean")
    expected = [s["fingerprint_pre"] for s in manifest["steps"]]
    result = await loop.run_flow(
        _flow(), allow_llm=False, expected_fingerprints=expected
    )
    assert result["status"] == "passed", result
    assert all("drift" not in step for step in result["steps"])


async def test_run_replay_falls_back_and_heals_mutated_page(
    tmp_path, monkeypatch
):
    """Mutated page (live fp != manifest): replay drifts, one agentic
    re-run heals, and the drift stays on the record."""

    def _boom(*args, **kwargs):
        raise AssertionError("LLM must never be constructed without credentials")

    monkeypatch.setattr("src.llm.client.LLMClient", _boom)
    manifest = await _author(tmp_path, monkeypatch)

    async def mutated_fingerprint(runtime):
        return "mutated-page-state"

    monkeypatch.setattr(
        "src.agent.recovery.dom_fingerprint", mutated_fingerprint
    )
    # NOTE: loop.py holds the same module object, so the gate sees it too.
    import src.agent.loop as loop_module

    assert loop_module.recovery.dom_fingerprint is mutated_fingerprint

    async def dispatch(payload, *args, **kwargs):
        return "Observing current page state"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)
    loop = _make_loop(tmp_path, run_id="drift-heal")
    result = await run_replay(manifest=manifest, loop=loop)
    assert result["status"] == "passed", result
    assert result["replay"]["mode"] == "replay+fallback"
    assert result["replay"]["first_status"] == "failed"
    assert result["replay"]["drift"]["suspected"] is True
    assert result["replay"]["drift"]["signal"] == DRIFT_SUSPECTED
    assert result["replay"]["fallback"] == {
        "attempted": True,
        "runs": 1,
        "healed": True,
    }


async def test_run_replay_without_fallback_reports_drift(tmp_path, monkeypatch):
    manifest = await _author(tmp_path, monkeypatch)

    async def mutated_fingerprint(runtime):
        return "mutated-page-state"

    monkeypatch.setattr(
        "src.agent.recovery.dom_fingerprint", mutated_fingerprint
    )

    async def dispatch(payload, *args, **kwargs):
        return "Observing current page state"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)
    loop = _make_loop(tmp_path, run_id="drift-nofallback")
    result = await run_replay(
        manifest=manifest, loop=loop, allow_fallback=False
    )
    assert result["status"] == "failed", result
    assert result["replay"]["mode"] == "replay"
    assert result["replay"]["drift"]["suspected"] is True
    assert result["replay"]["fallback"]["attempted"] is False


async def test_failed_step_without_drift_triggers_fallback(tmp_path, monkeypatch):
    """No drift, but replayed actions fail: fallback still engages and heals.

    The first two dispatches (replay pass) fail; the fallback pass
    dispatches cleanly. Fingerprints match throughout, so no drift is
    ever suspected — the trigger is purely the failed step.
    """
    manifest = await _author(tmp_path, monkeypatch)
    calls = {"n": 0}

    async def dispatch(payload, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            return "[Failed] element not found"
        return "Observing current page state"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)
    loop = _make_loop(tmp_path, run_id="drift-failstep-heal")
    result = await run_replay(manifest=manifest, loop=loop)
    assert result["status"] == "passed", result
    assert result["replay"]["first_status"] == "failed"
    assert result["replay"]["drift"]["suspected"] is False
    assert result["replay"]["fallback"]["healed"] is True
