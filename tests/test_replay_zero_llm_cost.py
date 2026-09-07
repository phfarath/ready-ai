"""PH3C — deterministic replay is zero-token: ``*_flow_metrics.json`` proves
a zero-LLM cost ledger (READY-AI-T-PH3-ZERO-TOKEN-REPLAY).

The engine already writes a per-run metrics artifact
(``src/agent/loop._save_flow_metrics`` -> ``run_summary``) with the LLM
cost counters ``llm_calls``, ``llm_tokens.{prompt,completion}`` and
``llm_cost_usd``. The before/after cost metric published on the card is
the *difference* between the authoring run (LLM, may cost > 0) and the
replay run (must cost exactly 0).

Contract (persisted in Strata/Cortex memory under project ready-ai):
  - replay (``run_flow(allow_llm=False)``) MUST report
    ``llm_calls == 0``, ``llm_tokens == {prompt: 0, completion: 0}``,
    ``llm_cost_usd == 0.0`` in ``*_flow_metrics.json``.
  - the authoring run metrics artifact exists with the same keys, so the
    before/after delta is computable from disk alone.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.loop import AgenticLoop
from src.api.models import FlowAction, FlowAssertion, FlowSpec, FlowStepSpec


def _flow(steps):
    return FlowSpec(
        name="cost-flow",
        url="https://app.example.com/start",
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
            FlowStepSpec(name="Look", actions=[FlowAction(action="observe")]),
        ]
    )


def _make_loop(tmp_path, run_id="cost-replay"):
    loop = AgenticLoop(
        goal="cost",
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
    runtime.evaluate = AsyncMock(return_value="dom-state")
    runtime.query_selector = AsyncMock(return_value=None)
    runtime.get_element_text = AsyncMock(return_value="")
    runtime.get_visible_text = AsyncMock(return_value="")
    runtime.get_element_attributes = AsyncMock(return_value={})
    session._page = page
    session._runtime = runtime
    session._input = MagicMock()
    return loop, runtime


async def _observe_dispatch(payload, *args, **kwargs):
    return "Observing current page state"


async def _run_flow(loop, flow, allow_llm):
    result = await loop.run_flow(flow, allow_llm=allow_llm)
    assert result["status"] == "passed", result
    return result


async def test_replay_flow_metrics_report_zero_llm_cost(tmp_path, monkeypatch):
    """Replay of a compiled manifest writes a metrics artifact proving zero
    LLM calls, zero tokens and zero cost."""
    from src.agent.replay import (
        compile_manifest,
        manifest_to_flow_spec,
        read_manifest,
        write_manifest,
    )

    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", _observe_dispatch)
    author_loop, _runtime = _make_loop(tmp_path, run_id="cost-author")
    author_loop._session._runtime.evaluate = AsyncMock(
        return_value="https://app.example.com/start"
    )
    authored = await _run_flow(author_loop, _observe_flow(), allow_llm=True)

    manifest_path = write_manifest(
        compile_manifest(_observe_flow(), authored), tmp_path, "cost-author"
    )
    replay_loop, _runtime = _make_loop(tmp_path, run_id="cost-replay")
    replay_loop._session._runtime.evaluate = AsyncMock(
        return_value="https://app.example.com/start"
    )
    await _run_flow(
        replay_loop, manifest_to_flow_spec(read_manifest(manifest_path)), allow_llm=False
    )

    metrics_path = tmp_path / "cost-replay_flow_metrics.json"
    assert metrics_path.is_file(), "replay run must write a flow metrics artifact"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics["llm_calls"] == 0
    assert metrics["llm_tokens"] == {"prompt": 0, "completion": 0}
    assert metrics["llm_cost_usd"] == 0.0


async def test_author_metrics_artifact_enables_before_after_delta(tmp_path, monkeypatch):
    """The authoring run also writes ``*_flow_metrics.json`` with the same cost
    keys, so the before/after delta is computable from disk alone."""
    monkeypatch.setattr("src.agent.loop.executor._dispatch_action", _observe_dispatch)
    author_loop, _runtime = _make_loop(tmp_path, run_id="cost-author")
    author_loop._session._runtime.evaluate = AsyncMock(
        return_value="https://app.example.com/start"
    )
    await _run_flow(author_loop, _observe_flow(), allow_llm=True)

    metrics_path = tmp_path / "cost-author_flow_metrics.json"
    assert metrics_path.is_file()
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert "llm_calls" in metrics
    assert "llm_tokens" in metrics
    assert metrics["llm_tokens"]["prompt"] == 0
    assert metrics["llm_tokens"]["completion"] == 0
    assert "llm_cost_usd" in metrics


# ─── SDK wiring (PH3C): ReadyAI.replay_manifest → run_replay + cost ────────


def _passthrough_step():
    return {
        "index": 1,
        "name": "Land",
        "status": "passed",
        "actions": [],
        "asserts": [],
        "extracted": [],
        "attempts": 1,
    }


def _replay_data(**overrides):
    data = {
        "run_id": "replay-abc",
        "flow": "checkout",
        "url": "https://app.example.com/start",
        "status": "passed",
        "steps": [_passthrough_step()],
        "summary": {"steps_total": 1, "steps_passed": 1},
        "failure_reason": None,
        "replay": {
            "mode": "replay",
            "first_status": "passed",
            "drift": {"suspected": False, "signal": "", "drifts": []},
            "fallback": {"attempted": False, "runs": 0, "healed": False},
        },
    }
    data.update(overrides)
    return data


async def test_sdk_replay_manifest_forwards_fallback_and_exposes_replay_block(
    tmp_path, monkeypatch
):
    from ready_ai import ReadyAI

    fake_run_replay = AsyncMock(return_value=_replay_data())
    monkeypatch.setattr("src.agent.replay.run_replay", fake_run_replay)
    monkeypatch.setattr("ready_ai.client.AgenticLoop", lambda **kw: object())

    ai = ReadyAI(output_dir=str(tmp_path))
    manifest = {"source_run_id": "author-123", "url": "https://app.example.com/start"}
    result = await ai.replay_manifest(
        manifest,
        fallback_agentic=True,
        max_fallback_runs=2,
        confirm=["flow:guard"],
    )

    call = fake_run_replay.call_args
    assert call.kwargs["allow_fallback"] is True
    assert call.kwargs["max_fallback_runs"] == 2
    assert call.kwargs["confirm"] == ["flow:guard"]
    assert call.kwargs["manifest"] is manifest

    assert result.status == "passed"
    assert result.replay is not None
    assert result.replay["mode"] == "replay"
    # No metrics artifacts on disk => degraded ledger, still zero-token.
    assert result.cost is not None
    assert result.cost["zero_token"] is True


async def test_sdk_replay_manifest_defaults_are_fail_closed(tmp_path, monkeypatch):
    from ready_ai import ReadyAI

    fake_run_replay = AsyncMock(return_value=_replay_data())
    monkeypatch.setattr("src.agent.replay.run_replay", fake_run_replay)
    monkeypatch.setattr("ready_ai.client.AgenticLoop", lambda **kw: object())

    ai = ReadyAI(output_dir=str(tmp_path))
    await ai.replay_manifest({"url": "https://app.example.com/start"})

    call = fake_run_replay.call_args
    assert call.kwargs["allow_fallback"] is False
    assert call.kwargs["max_fallback_runs"] == 1
    assert call.kwargs["confirm"] is None


def test_sdk_cost_ledger_before_after_from_metrics_artifacts(tmp_path):
    from ready_ai import ReadyAI

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
    (tmp_path / "author-123_flow_metrics.json").write_text(
        json.dumps(author), encoding="utf-8"
    )
    (tmp_path / "replay-456_flow_metrics.json").write_text(
        json.dumps(replay), encoding="utf-8"
    )

    ai = ReadyAI(output_dir=str(tmp_path))
    cost = ai._replay_cost_ledger("author-123", "replay-456", str(tmp_path))
    assert cost["before"]["llm_calls"] == 6
    assert cost["before"]["llm_cost_usd"] == pytest.approx(0.0420)
    assert cost["before"]["llm_prompt_tokens"] == 1200
    assert cost["after"]["llm_calls"] == 0
    assert cost["zero_token"] is True
    assert cost["saved_usd"] == pytest.approx(0.0420)

    # An unknown author run degrades to zeros — the ledger never raises.
    zeros = ai._replay_cost_ledger("ghost-author", "replay-456", str(tmp_path))
    assert zeros["before"]["llm_calls"] == 0
    assert zeros["after"]["llm_cost_usd"] == 0.0
    assert zeros["zero_token"] is True