"""
Tests for the multi-channel healing gate (READY-AI-T-US5).

Auto-heal of screenshots/annotations requires >=2 drift channels of distinct
causal natures to agree (visual render change + structural DOM change).
Single-channel drift is reclassified as DRIFT_SUSPECTED for human review.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.agent.test_runner import DocTestReport, StepTestResult
from src.docs.auto_healer import (
    HEAL_GATE_MIN_CHANNELS_ENV,
    DocAutoHealer,
    DriftGateDecision,
    evaluate_drift_gate,
)
from src.docs.healing_publisher import _render_pr_body


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sample_doc"


# ─── Unit: evaluate_drift_gate ────────────────────────────────────────────


class TestEvaluateDriftGate:
    def test_two_channels_agree_is_eligible(self):
        decision = evaluate_drift_gate(0.50, 0.85, True)

        assert isinstance(decision, DriftGateDecision)
        assert decision.eligible is True
        assert decision.channels_agreeing == ["visual", "structural"]
        assert decision.channels_conflicting == []

    def test_visual_only_not_eligible_structural_conflicting(self):
        decision = evaluate_drift_gate(0.50, 0.85, False)

        assert decision.eligible is False
        assert decision.channels_agreeing == ["visual"]
        assert "structural" in decision.channels_conflicting

    def test_structural_only_not_eligible_visual_conflicting(self):
        # A run would never call the gate in this state (sim >= threshold is
        # not DRIFT), but the pure function must still be correct.
        decision = evaluate_drift_gate(0.95, 0.85, True)

        assert decision.eligible is False
        assert decision.channels_agreeing == ["structural"]
        assert "visual" in decision.channels_conflicting

    def test_no_channels_agree_both_conflicting(self):
        decision = evaluate_drift_gate(0.99, 0.85, False)

        assert decision.eligible is False
        assert decision.channels_agreeing == []
        assert decision.channels_conflicting == ["visual", "structural"]

    def test_min_channels_one_via_env_makes_single_channel_eligible(
        self, monkeypatch
    ):
        monkeypatch.setenv(HEAL_GATE_MIN_CHANNELS_ENV, "1")

        decision = evaluate_drift_gate(0.50, 0.85, False)

        assert decision.eligible is True
        assert decision.channels_agreeing == ["visual"]

    def test_invalid_env_falls_back_to_default_two(self, monkeypatch):
        monkeypatch.setenv(HEAL_GATE_MIN_CHANNELS_ENV, "banana")

        single = evaluate_drift_gate(0.50, 0.85, False)
        double = evaluate_drift_gate(0.50, 0.85, True)

        assert single.eligible is False
        assert double.eligible is True

    def test_env_below_one_clamps_to_one_never_zero(self, monkeypatch):
        monkeypatch.setenv(HEAL_GATE_MIN_CHANNELS_ENV, "0")

        decision = evaluate_drift_gate(0.50, 0.85, False)

        assert decision.eligible is True

    def test_negative_env_clamps_to_one(self, monkeypatch):
        monkeypatch.setenv(HEAL_GATE_MIN_CHANNELS_ENV, "-3")

        assert evaluate_drift_gate(0.50, 0.85, True).eligible is True

    def test_explicit_min_channels_overrides_env(self, monkeypatch):
        monkeypatch.setenv(HEAL_GATE_MIN_CHANNELS_ENV, "1")

        decision = evaluate_drift_gate(0.50, 0.85, False, min_channels=2)

        assert decision.eligible is False

    def test_explicit_min_channels_below_one_clamps_to_one(self):
        decision = evaluate_drift_gate(0.50, 0.85, False, min_channels=0)

        assert decision.eligible is True

    def test_env_unset_defaults_to_two(self, monkeypatch):
        monkeypatch.delenv(HEAL_GATE_MIN_CHANNELS_ENV, raising=False)

        single = evaluate_drift_gate(0.50, 0.85, False)
        double = evaluate_drift_gate(0.50, 0.85, True)

        assert single.eligible is False
        assert double.eligible is True


# ─── Integration: DocAutoHealer.heal_report with gate ─────────────────────


@pytest.fixture
def doc_dir(tmp_path):
    """Copy fixture docs to a temp dir so tests don't pollute fixtures."""
    dest = tmp_path / "sample_doc"
    shutil.copytree(FIXTURE_DIR, dest)
    return dest


def _make_llm(annotation: str = "Fresh annotation.") -> AsyncMock:
    llm = AsyncMock()
    llm.complete_with_vision = AsyncMock(return_value=annotation)
    llm.complete = AsyncMock(return_value="{}")
    return llm


def _make_report(doc_dir: Path, results: list[StepTestResult]) -> DocTestReport:
    return DocTestReport(
        doc_path=str(doc_dir / "docs.md"),
        url="http://localhost:8080",
        timestamp="2026-08-22T12:00:00",
        threshold=0.85,
        results=results,
    )


def _drift_result(
    step_number: int,
    doc_dir: Path,
    *,
    visual_similarity: float = 0.5,
    dom_changed: bool = True,
) -> StepTestResult:
    shot = doc_dir / f"new_step_{step_number:02d}.png"
    shot.write_bytes(f"drifted-png-{step_number}".encode())
    return StepTestResult(
        step_number=step_number,
        title=f"Step {step_number}",
        status="DRIFT",
        visual_similarity=visual_similarity,
        dom_changed=dom_changed,
        new_screenshot_path=str(shot),
    )


@pytest.mark.asyncio
async def test_two_channels_agree_heals_and_logs_both(doc_dir):
    result = _drift_result(1, doc_dir, visual_similarity=0.4, dom_changed=True)
    report = _make_report(doc_dir, [result])
    healer = DocAutoHealer(str(doc_dir / "docs.md"), _make_llm())

    healing = await healer.heal_report(report)

    assert report.results[0].status == "DRIFT"
    assert healing.total_healed == 1
    assert healing.doc_rewritten is True
    assert len(healing.gate_log) == 1
    entry = healing.gate_log[0]
    assert entry.step_number == 1
    assert entry.decision == "healed"
    assert entry.channels_agreeing == ["visual", "structural"]
    assert entry.channels_conflicting == []
    # Screenshot baseline actually updated
    baseline = doc_dir / "screenshots" / "step_01.png"
    assert baseline.read_bytes() == b"drifted-png-1"
    # Annotation regenerated in doc
    doc_text = (doc_dir / "docs.md").read_text(encoding="utf-8")
    assert "Fresh annotation." in doc_text


@pytest.mark.asyncio
async def test_single_channel_drift_flags_suspected_without_healing(doc_dir):
    result = _drift_result(1, doc_dir, visual_similarity=0.4, dom_changed=False)
    report = _make_report(doc_dir, [result])
    healer = DocAutoHealer(str(doc_dir / "docs.md"), _make_llm())

    doc_before = (doc_dir / "docs.md").read_bytes()
    baseline_before = (doc_dir / "screenshots" / "step_01.png").read_bytes()

    healing = await healer.heal_report(report)

    assert report.results[0].status == "DRIFT_SUSPECTED"
    assert healing.total_healed == 0
    assert healing.steps_healed == []
    assert healing.doc_rewritten is False
    # docs.md and baselines are byte-identical
    assert (doc_dir / "docs.md").read_bytes() == doc_before
    assert (doc_dir / "screenshots" / "step_01.png").read_bytes() == baseline_before
    # Gate log records the conflict
    assert len(healing.gate_log) == 1
    entry = healing.gate_log[0]
    assert entry.step_number == 1
    assert entry.decision == "suspected"
    assert entry.channels_agreeing == ["visual"]
    assert "structural" in entry.channels_conflicting


@pytest.mark.asyncio
async def test_guardrail_assertions_outcome_unchanged_after_heal(doc_dir):
    result = _drift_result(1, doc_dir, visual_similarity=0.4, dom_changed=True)
    report = _make_report(doc_dir, [result])
    healer = DocAutoHealer(str(doc_dir / "docs.md"), _make_llm())

    await healer.heal_report(report)

    doc_text = (doc_dir / "docs.md").read_text(encoding="utf-8")
    # Expected action/assertion text untouched
    assert (
        "**Action executed:** Clicked element: button#login-btn" in doc_text
    )
    # Step structure untouched
    assert "## Step 1: Click the Login button" in doc_text
    assert "<details>" in doc_text and "</details>" in doc_text
    # Old annotation is gone (replaced), everything else intact
    assert (
        'Click the "Login" button in the top navigation bar'
        not in doc_text
    )
    assert "## Step 2: Verify the dashboard loads" in doc_text


@pytest.mark.asyncio
async def test_mixed_report_heals_confirmed_flags_suspected(doc_dir):
    confirmed = _drift_result(1, doc_dir, visual_similarity=0.4, dom_changed=True)
    suspected = _drift_result(2, doc_dir, visual_similarity=0.4, dom_changed=False)
    report = _make_report(doc_dir, [confirmed, suspected])
    healer = DocAutoHealer(str(doc_dir / "docs.md"), _make_llm())

    healing = await healer.heal_report(report)

    assert report.results[0].status == "DRIFT"
    assert report.results[1].status == "DRIFT_SUSPECTED"
    assert healing.total_healed == 1
    assert [e.decision for e in healing.gate_log] == ["healed", "suspected"]
    assert healing.steps_suspected == [2]
    doc_text = (doc_dir / "docs.md").read_text(encoding="utf-8")
    assert "Fresh annotation." in doc_text  # step 1 healed
    # Step 2 annotation untouched
    assert "After logging in, verify that the dashboard page" in doc_text


@pytest.mark.asyncio
async def test_recover_selector_works_independently_of_gate(doc_dir):
    """recover_selector stays exempt from the drift gate (verified upstream)."""
    import json

    llm = _make_llm()
    llm.complete = AsyncMock(
        return_value=json.dumps(
            {
                "found": True,
                "selector": "button#new-login",
                "reason": "renamed id",
            }
        )
    )
    healer = DocAutoHealer(str(doc_dir / "docs.md"), llm)

    heal = await healer.recover_selector(
        1,
        "Clicked element: button#login-btn",
        '[{"tag": "button", "id": "new-login", "text": "Login"}]',
    )

    assert heal.selector_recovered is True
    assert heal.new_selector == "button#new-login"
    # recover_selector mutates the in-memory doc content; persistence to
    # disk belongs to heal_report.
    assert (
        "**Action executed:** Clicked element: button#new-login"
        in healer._doc_content
    )


# ─── Downstream surfaces ──────────────────────────────────────────────────


def test_summary_counts_drift_suspected():
    report = DocTestReport(
        doc_path="docs.md",
        url="http://localhost:8080",
        timestamp="2026-08-22T12:00:00",
        threshold=0.85,
        results=[
            StepTestResult(
                step_number=1,
                title="Step 1",
                status="DRIFT_SUSPECTED",
                visual_similarity=0.4,
                dom_changed=False,
            ),
            StepTestResult(
                step_number=2,
                title="Step 2",
                status="PASSED",
                visual_similarity=1.0,
                dom_changed=False,
            ),
        ],
        overall_status="DRIFT_DETECTED",
        # Detection-side bookkeeping is unchanged by the gate
        steps_outdated=[1],
    )

    summary = report.summary()

    assert "1 drift-suspected (awaiting human review)" in summary
    assert "Outdated steps: [1]" in summary


def test_pr_body_lists_suspected_steps_for_human_review():
    from src.docs.auto_healer import DriftGateLogEntry, HealResult, HealingReport

    healing = HealingReport(doc_rewritten=True)
    healing.steps_healed = [
        HealResult(step_number=1, screenshot_updated=True)
    ]
    healing.gate_log = [
        DriftGateLogEntry(
            step_number=1,
            decision="healed",
            channels_agreeing=["visual", "structural"],
        ),
        DriftGateLogEntry(
            step_number=2,
            decision="suspected",
            channels_agreeing=["visual"],
            channels_conflicting=["structural"],
        ),
    ]

    class FakeResult:
        def __init__(self, step_number, title):
            self.step_number = step_number
            self.title = title

    class FakeReport:
        overall_status = "DRIFT_DETECTED"
        results = [
            FakeResult(1, "Click login button"),
            FakeResult(2, "Verify dashboard"),
        ]

    body = _render_pr_body(
        healing_report=healing,
        doc_test_report=FakeReport(),
        html_report_path=None,
        doc_path=Path("docs/docs.md"),
    )

    assert "### Suspected drift — awaiting human review" in body
    assert "Step 2 (Verify dashboard)" in body
    assert "agreeing channels: visual; conflicting: structural" in body
    assert "**not** auto-healed" in body
