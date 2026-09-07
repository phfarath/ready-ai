"""Contract tests for the PH3B drift gate — REAL engine/SDK interface
(READY-AI-T-PH3-ZERO-TOKEN-REPLAY).

Interface final (implemented by the parallel engine work; persisted in
Strata/Cortex memory under project ready-ai):
  - ``AgenticLoop.run_flow(allow_llm=False, expected_fingerprints=...)``:
    the pre-actuation gate compares the live pre-step fingerprint against
    the manifest's; a divergence fails the step with a ``drift`` block
    (``signal == DRIFT_SUSPECTED``) WITHOUT dispatching any action.
  - ``src.agent.replay``: ``extract_fingerprints`` / ``drifted_step_report`` /
    ``collect_drifts`` / ``run_replay(manifest, loop, allow_fallback,
    max_fallback_runs)`` — deterministic pass one, bounded agentic heal pass
    on drift OR failed step, always observable via the ``replay`` block.
  - ``ready_ai.replay`` (SDK): ``compare_fingerprints`` / ``failed_steps`` /
    ``detect_drift`` / ``summarize_replay_cost`` (PH3C before/after ledger).

Focus here: gap coverage on top of the engine's own drift tests — purity of
the report/collections, the bounded fallback budget, the zero-LLM invariant
on the drift path, the metrics counter, and the SDK-side drift/cost
interpretation (including ``zero_token``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ready_ai import replay as sdk_replay
from src.agent import replay as engine_replay
from src.agent.loop import AgenticLoop
from src.api.models import FlowAction, FlowAssertion, FlowSpec, FlowStepSpec


def _flow():
    return FlowSpec(
        name="drift-flow",
        url="https://app.example.com/start",
        steps=[
            FlowStepSpec(
                name="Land",
                actions=[FlowAction(action="observe")],
                asserts=[
                    FlowAssertion(type="url_contains", expected="app.example.com")
                ],
            ),
            FlowStepSpec(name="Look", actions=[FlowAction(action="observe")]),
        ],
    )


def _make_loop(tmp_path, run_id="drift-test"):
    loop = AgenticLoop(
        goal="drift",
        url="https://app.example.com/start",
        output_dir=str(tmp_path),
        run_id=run_id,
        headless=True,
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
    return engine_replay.compile_manifest(_flow(), result)


# ─── Pure helpers ───────────────────────────────────────────────────────────


def test_drifted_step_report_matches_return_none():
    report = engine_replay.drifted_step_report(
        index=1,
        name="Land",
        key="run:step-1",
        expected="fp-1",
        actual="fp-1",
        asserts_count=1,
        extract_count=0,
    )
    assert report is None


def test_drifted_step_report_signal_and_no_actions():
    report = engine_replay.drifted_step_report(
        index=2,
        name="Look",
        key="run:step-2",
        expected="fp-1",
        actual="fp-CHANGED",
        asserts_count=0,
        extract_count=1,
    )
    assert report is not None
    assert report["status"] == "failed"
    assert report["actions"] == []
    assert report["asserts"] == []
    assert report["skipped_asserts"] == 0
    assert report["skipped_extractions"] == 1
    assert report["drift"]["signal"] == engine_replay.DRIFT_SUSPECTED
    assert report["drift"]["expected"] == "fp-1"
    assert report["drift"]["actual"] == "fp-CHANGED"


def test_drifted_step_report_gate_off_when_expected_none():
    off = engine_replay.drifted_step_report(
        index=1,
        name="x",
        key="k",
        expected=None,
        actual="anything",
        asserts_count=0,
        extract_count=0,
    )
    assert off is None


def test_extract_fingerprints_preserves_authoring_order():
    manifest = {
        "steps": [
            {"fingerprint_pre": "fp-1"},
            {"name": "no-fp"},
            {"fingerprint_pre": "fp-3"},
        ]
    }
    assert engine_replay.extract_fingerprints(manifest) == ["fp-1", "", "fp-3"]


def test_collect_drifts_is_pure_gate_trip():
    expected = ["fp-1", "fp-2"]
    steps = [
        {"index": 1, "fingerprint_pre": "fp-1", "status": "passed"},
        {
            "index": 2,
            "status": "failed",
            "drift": {
                "signal": engine_replay.DRIFT_SUSPECTED,
                "expected": "fp-2",
                "actual": "fp-mutated",
            },
        },
    ]
    drifts = engine_replay.collect_drifts(expected, steps)
    assert len(drifts) == 1
    assert drifts[0]["reason"] == "gate-trip"
    assert drifts[0]["index"] == 2


def test_collect_drifts_detects_divergence_and_count_mismatch():
    # Trust-live heal pass: no gate-trip markers, but live fingerprint
    # diverged from the manifest (page genuinely changed).
    expected = ["fp-1", "fp-2"]
    steps = [
        {"index": 1, "fingerprint_pre": "fp-1"},
        {"index": 2, "fingerprint_pre": "fp-MUTATED"},
    ]
    drifts = engine_replay.collect_drifts(expected, steps)
    assert any(d["reason"] == "fingerprint-diverged" for d in drifts)

    # Step-count mismatch is surfaced too.
    drifts_short = engine_replay.collect_drifts(expected, steps[:1])
    assert any(d["reason"] == "step-count-mismatch" for d in drifts_short)


# ─── Loop / run_replay behavior ─────────────────────────────────────────────


async def test_replay_gate_never_constructs_llm_on_drift(tmp_path, monkeypatch):
    """Drifted step: gate trips BEFORE actuation, LLM never constructed."""
    manifest = await _author(tmp_path, monkeypatch)
    calls: list = []

    async def dispatch(payload, *args, **kwargs):
        calls.append(payload)
        return "Observing current page state"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)

    def _boom(*args, **kwargs):
        raise AssertionError("LLM must never be constructed during replay")

    monkeypatch.setattr("src.llm.client.LLMClient", _boom)

    loop = _make_loop(tmp_path, run_id="drift-zero-llm")
    # Live DOM diverges from the authoring fingerprint.
    loop._session._runtime.evaluate = AsyncMock(return_value="mutated-page-state")
    expected = engine_replay.extract_fingerprints(manifest)

    result = await loop.run_flow(
        _flow(), allow_llm=False, expected_fingerprints=expected
    )
    assert result["status"] == "failed"
    assert calls == [], "the drift gate must short-circuit before any action"
    assert all(s.get("drift", {}).get("signal") == engine_replay.DRIFT_SUSPECTED for s in result["steps"])
    assert all(s["actions"] == [] for s in result["steps"])


async def test_drift_metric_counter_increments(tmp_path, monkeypatch):
    from src.observability import get_metrics

    manifest = await _author(tmp_path, monkeypatch)

    async def dispatch(payload, *args, **kwargs):
        return "Observing current page state"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)
    loop = _make_loop(tmp_path, run_id="drift-metric")
    loop._session._runtime.evaluate = AsyncMock(return_value="mutated-page-state")

    await loop.run_flow(
        _flow(),
        allow_llm=False,
        expected_fingerprints=engine_replay.extract_fingerprints(manifest),
    )
    metrics = get_metrics()
    assert metrics is not None
    assert metrics.get_counter("replay.drift_suspected") >= 1


async def test_run_replay_no_fallback_reports_drift(tmp_path, monkeypatch):
    manifest = await _author(tmp_path, monkeypatch)

    async def dispatch(payload, *args, **kwargs):
        return "Observing current page state"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", dispatch)
    loop = _make_loop(tmp_path, run_id="replay-nofallback")
    loop._session._runtime.evaluate = AsyncMock(return_value="mutated-page-state")

    result = await engine_replay.run_replay(
        manifest=manifest, loop=loop, allow_fallback=False
    )
    assert result["status"] == "failed"
    assert result["replay"]["mode"] == "replay"
    assert result["replay"]["first_status"] == "failed"
    assert result["replay"]["drift"]["suspected"] is True
    assert result["replay"]["drift"]["signal"] == engine_replay.DRIFT_SUSPECTED
    assert result["replay"]["fallback"] == {
        "attempted": False,
        "runs": 0,
        "healed": False,
    }


async def test_run_replay_respects_bounded_fallback_budget(tmp_path, monkeypatch):
    """Step failure (no drift) triggers the heal pass, but the budget caps it."""
    manifest = await _author(tmp_path, monkeypatch)

    async def failing_dispatch(payload, *args, **kwargs):
        return "[Failed] element not found"

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", failing_dispatch)
    loop = _make_loop(tmp_path, run_id="replay-budget")

    result = await engine_replay.run_replay(
        manifest=manifest, loop=loop, max_fallback_runs=2
    )
    assert result["status"] == "failed"
    assert result["replay"]["mode"] == "replay+fallback"
    assert result["replay"]["fallback"]["attempted"] is True
    assert result["replay"]["fallback"]["runs"] == 2
    assert result["replay"]["fallback"]["healed"] is False


# ─── SDK interpretation (ready_ai.replay) ───────────────────────────────────


def _fingerprinted_result(drifted=False):
    # Mirrors a real engine step report: on drift the report's
    # ``fingerprint_pre`` holds the LIVE (diverged) fingerprint, not the
    # manifest expectation. The SDK interprets via fingerprint_pre only.
    steps = [
        {"index": 1, "name": "Land", "status": "passed", "fingerprint_pre": "fp-1"},
        {"index": 2, "name": "Look", "status": "failed", "fingerprint_pre": "fp-2"},
    ]
    if drifted:
        steps[1]["fingerprint_pre"] = "mutated"
        steps[1]["drift"] = {
            "signal": engine_replay.DRIFT_SUSPECTED,
            "expected": "fp-2",
            "actual": "mutated",
        }
    return {
        "status": "failed" if (drifted or steps[1]["status"] != "passed") else "passed",
        "steps": steps,
    }


def test_sdk_compare_fingerprints_divergence_and_missing():
    manifest = {
        "steps": [
            {"fingerprint_pre": "fp-1"},
            {"fingerprint_pre": "fp-2"},
        ]
    }
    clean = [
        {"index": 1, "fingerprint_pre": "fp-1"},
        {"index": 2, "fingerprint_pre": "fp-2"},
    ]
    assert sdk_replay.compare_fingerprints(manifest, clean) == []

    mutated = [
        {"index": 1, "fingerprint_pre": "fp-1"},
        {"index": 2, "fingerprint_pre": "fp-MUTATED"},
    ]
    drifts = sdk_replay.compare_fingerprints(manifest, mutated)
    assert len(drifts) == 1
    assert drifts[0]["reason"] == "fingerprint-diverged"
    assert drifts[0]["expected"] == "fp-2"


def test_sdk_detect_drift_combines_failed_steps_and_fingerprints():
    manifest = {"steps": [{"fingerprint_pre": "fp-1"}, {"fingerprint_pre": "fp-2"}]}
    gate = sdk_replay.detect_drift(manifest, _fingerprinted_result(drifted=True))
    assert gate["drift_suspected"] is True
    assert gate["signal"] == sdk_replay.DRIFT_SUSPECTED
    assert any(d["reason"] == "fingerprint-diverged" for d in gate["drifts"])
    assert gate["failed_steps"] and gate["failed_steps"][0]["status"] == "failed"


def test_sdk_detect_drift_false_when_clean():
    manifest = {"steps": [{"fingerprint_pre": "fp-1"}, {"fingerprint_pre": "fp-2"}]}
    clean = {
        "status": "passed",
        "steps": [
            {"index": 1, "status": "passed", "fingerprint_pre": "fp-1"},
            {"index": 2, "status": "passed", "fingerprint_pre": "fp-2"},
        ],
    }
    gate = sdk_replay.detect_drift(manifest, clean)
    assert gate["drift_suspected"] is False
    assert gate["signal"] == ""


def test_sdk_summarize_replay_cost_zero_token():
    author = {
        "llm_calls": 6,
        "llm_tokens": {"prompt": 1200, "completion": 400},
        "llm_cost_usd": 0.0420,
    }
    replay = {
        "llm_calls": 0,
        "llm_tokens": {"prompt": 0, "completion": 0},
        "llm_cost_usd": 0.0,
    }
    out = sdk_replay.summarize_replay_cost(author, replay)
    assert out["zero_token"] is True
    assert out["before"]["llm_calls"] == 6
    assert out["after"]["llm_cost_usd"] == 0.0
    assert out["after"]["total_cost_usd"] == 0.0
    assert out["after"]["heal_cost_usd"] == 0.0
    assert out["saved_usd"] == pytest.approx(0.0420)


def test_sdk_summarize_replay_cost_counts_heal_apart():
    author = {"llm_calls": 6, "llm_tokens": {}, "llm_cost_usd": 1.0}
    replay = {"llm_calls": 0, "llm_tokens": {}, "llm_cost_usd": 0.0}
    out = sdk_replay.summarize_replay_cost(
        author, replay, heal_cost_usd=0.5
    )
    assert out["zero_token"] is False
    assert out["after"]["heal_cost_usd"] == 0.5
    assert out["after"]["total_cost_usd"] == 0.5
    assert out["saved_usd"] == pytest.approx(0.5)