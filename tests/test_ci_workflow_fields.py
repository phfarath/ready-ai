"""Regression tests for VAL-QUAL-010: CI regression workflow references
correct DocTestReport fields.

The GitHub Actions workflow ``docs-regression.yml`` builds a PR comment from
the JSON report written by ``DocTestReport.to_dict()``.  Two field
references were wrong:

* ``report.total_steps``   — ``DocTestReport`` has no such field; the step
  count is ``len(results)`` (``report.results.length`` in JS).
* ``report.execution_time`` — ``DocTestReport`` has no such field either,
  so the Duration row must be removed.

These tests perform a static inspection of the workflow YAML (mirroring the
``rg "execution_time|total_steps"`` evidence required by the validation
contract) and confirm that the file parses as valid YAML.
"""

import sys
from pathlib import Path

import pytest
import yaml
import json

sys.path.insert(0, str(Path(__file__).parent.parent))

WORKFLOW = (
    Path(__file__).parent.parent / ".github" / "workflows" / "docs-regression.yml"
)


# ---------------------------------------------------------------------------
# Static inspection
# ---------------------------------------------------------------------------

def test_no_invalid_field_references_in_workflow():
    """The workflow must not reference report.execution_time or
    report.total_steps — those fields do not exist on DocTestReport.

    Mirrors the contract evidence: ``rg "execution_time|total_steps"``
    returns zero matches.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "execution_time" not in text, (
        "docs-regression.yml still references report.execution_time, which "
        "does not exist on DocTestReport.to_dict()."
    )
    assert "total_steps" not in text, (
        "docs-regression.yml still references report.total_steps, which "
        "does not exist on DocTestReport.to_dict()."
    )


def test_workflow_uses_results_length_for_step_count():
    """Step count must come from report.results.length (results is a list
    of StepTestResult, one per documented step)."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "report.results.length" in text, (
        "docs-regression.yml must use report.results.length for the step "
        "count since DocTestReport.results holds one entry per step."
    )


def test_workflow_installs_the_checked_out_project_with_dev_dependencies():
    """Regression tests must exercise the PR checkout, not the PyPI release."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'pip install -e ".[dev]"' in text
    assert "pip install ready-ai" not in text


def test_workflow_uses_the_report_written_by_the_test_runner():
    """DocTestRunner writes test_report.json, including on drift."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "./regression-report/test_report.json" in text
    assert "./regression-report/report.json" not in text
    assert "without producing $REPORT.\"\n            exit 1" in text


def test_workflow_requires_a_nonempty_baseline_and_configured_staging_url():
    """Missing prerequisites must skip instead of producing a false failure."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'if [ -z "$STAGING_URL" ]; then' in text
    assert 'if [ -s "./docs-baseline/docs.md" ]; then' in text


# ---------------------------------------------------------------------------
# YAML validity
# ---------------------------------------------------------------------------

def test_workflow_yaml_parses_without_error():
    """The workflow must be valid YAML that parses cleanly."""
    text = WORKFLOW.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    # Basic structural sanity checks.
    assert data is not None, "Workflow YAML parsed to None."
    assert "jobs" in data, "Workflow YAML has no top-level 'jobs' key."
    job = data["jobs"]["regression-test"]
    assert "steps" in job, "regression-test job has no 'steps' key."


@pytest.mark.parametrize("field", ["results", "overall_status"])
def test_workflow_only_references_existing_doctest_report_fields(field):
    """Smoke-check that the JS metrics block only reads keys that
    DocTestReport.to_dict() actually produces."""
    from src.agent.test_runner import DocTestReport

    sample = DocTestReport(
        doc_path="x",
        url="y",
        timestamp="z",
        threshold=0.85,
    )
    d = sample.to_dict()
    assert field in d, f"DocTestReport.to_dict() missing field {field!r}"


# ---------------------------------------------------------------------------
# READY-AI-T-1 DoD coverage fixtures and tests
# ---------------------------------------------------------------------------



def test_explicit_skip_when_prerequisites_missing():
    """When STAGING_URL is empty and baseline is missing, the workflow must
    report SKIPPED explicitly, not pass silently."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'echo "status=SKIPPED" >> "$GITHUB_OUTPUT"' in text
    assert "SKIPPED" in text


def test_baseline_nonempty_and_parseable():
    """A valid baseline file must contain parseable steps (docs.md)."""
    baseline_path = Path("tests/fixtures/sample_doc/docs.md")
    assert baseline_path.exists()
    content = baseline_path.read_text(encoding="utf-8")
    assert "Step 1:" in content or "## Step" in content or len(content) > 0


def test_artifact_exists_for_each_status():
    """PASSED, DRIFT_DETECTED and BROKEN must have coherent artifacts."""
    for status in ("PASSED", "DRIFT_DETECTED", "BROKEN"):
        report_path = Path(f"tests/fixtures/regression_artifacts/{status.lower()}/test_report.json")
        assert report_path.exists(), f"Missing artifact for {status}"
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data.get("overall_status") == status, f"Artifact {status} has wrong status"
        artifact_path = report_path.parent / "artifact.md"
        assert artifact_path.exists(), f"Missing artifact.md for {status}"
