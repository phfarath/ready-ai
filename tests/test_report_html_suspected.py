"""TDD — report_html renders the baseline|current|diff triptych for
DRIFT_SUSPECTED steps (T-US5-FOLLOWUPS/3), not just DRIFT."""

from __future__ import annotations

import base64
from pathlib import Path

from src.agent.test_runner import DocTestReport, StepTestResult
from src.docs.report_html import render_html_report

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _report_with(status: str, tmp_path: Path) -> DocTestReport:
    diff = tmp_path / "diff_step_01.png"
    diff.write_bytes(_PNG)
    return DocTestReport(
        doc_path="docs.md",
        url="https://app.example.com",
        timestamp="2026-10-15T00:00:00Z",
        threshold=0.9,
        results=[
            StepTestResult(
                step_number=1,
                title="Land",
                status=status,
                visual_similarity=0.5,
                dom_changed=True,
                diff_image_path=str(diff),
            )
        ],
        overall_status="DRIFT_DETECTED",
    )


def test_drift_suspected_renders_diff_triptych(tmp_path):
    out = tmp_path / "report.html"
    render_html_report(_report_with("DRIFT_SUSPECTED", tmp_path), out)
    html = out.read_text(encoding="utf-8")
    assert "Baseline | Current | Diff" in html


def test_drift_still_renders_diff_triptych(tmp_path):
    out = tmp_path / "report.html"
    render_html_report(_report_with("DRIFT", tmp_path), out)
    html = out.read_text(encoding="utf-8")
    assert "Baseline | Current | Diff" in html
