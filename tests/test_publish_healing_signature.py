"""
Tests for the corrected publish_healing call signature (VAL-ROB-002).

These tests verify:
1. DocTestReport carries a ``healing_report`` attribute (default None).
2. ``_maybe_publish_healing`` with ``--open-pr`` but no healing logs "skipped"
   and does not crash.
3. ``_maybe_publish_healing`` with healing calls ``publish_healing`` with the
   correct 4-argument signature.
"""

from __future__ import annotations

import argparse
import logging
import subprocess


# ─── 1. DocTestReport.healing_report ───────────────────────────────────


def test_doctest_report_has_healing_report_default_none():
    """DocTestReport must have a healing_report attribute defaulting to None."""
    from src.agent.test_runner import DocTestReport

    report = DocTestReport(
        doc_path="test.md",
        url="http://example.com",
        timestamp="2026-01-01",
        threshold=0.85,
    )
    assert hasattr(report, "healing_report")
    assert report.healing_report is None


def test_doctest_report_healing_report_settable():
    """healing_report can be set to a HealingReport after construction."""
    from src.agent.test_runner import DocTestReport
    from src.docs.auto_healer import HealingReport

    healing = HealingReport(doc_rewritten=True)
    report = DocTestReport(
        doc_path="test.md",
        url="http://example.com",
        timestamp="2026-01-01",
        threshold=0.85,
        healing_report=healing,
    )
    assert report.healing_report is healing


# ─── 2. _maybe_publish_healing with no healing → "skipped" ────────────


def test_maybe_publish_healing_skipped_when_no_healing(tmp_path, caplog):
    """--open-pr without healing logs 'skipped' and does not crash."""
    import main as main_module
    from src.agent.test_runner import DocTestReport

    report = DocTestReport(
        doc_path=str(tmp_path / "docs.md"),
        url="http://example.com",
        timestamp="2026-01-01",
        threshold=0.85,
        # healing_report defaults to None
    )

    args = argparse.Namespace(
        doc=str(tmp_path / "docs.md"),
        output=str(tmp_path / "out"),
        pr_base_branch="dev",
        pr_remote="origin",
        pr_dry_run=True,
    )

    with caplog.at_level(logging.INFO):
        main_module._maybe_publish_healing(
            report, args, logging.getLogger("test")
        )

    # Must NOT raise; must log "skipped"
    assert any("skipped" in r.message.lower() for r in caplog.records), (
        f"Expected 'skipped' in log messages, got: {[r.message for r in caplog.records]}"
    )


# ─── 3. _maybe_publish_healing with healing → correct call signature ──


def test_maybe_publish_healing_calls_publish_with_correct_args(
    tmp_path, monkeypatch
):
    """With healing present, publish_healing is called with correct 4-arg signature."""
    import main as main_module
    from src.agent.test_runner import DocTestReport
    from src.docs.auto_healer import HealingReport

    # Set up a fake git repo so repo-root discovery works
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "docs.md").write_text("# doc\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    healing = HealingReport(doc_rewritten=True)
    healing.steps_healed = []
    report = DocTestReport(
        doc_path=str(repo / "docs" / "docs.md"),
        url="http://example.com",
        timestamp="2026-01-01",
        threshold=0.85,
        healing_report=healing,
    )

    captured: dict = {}

    def fake_publish(healing_report, doc_test_report, html_report_path, config):
        """Mock that enforces the REAL 4-arg signature."""
        captured["healing_report"] = healing_report
        captured["doc_test_report"] = doc_test_report
        captured["html_report_path"] = html_report_path
        captured["config"] = config
        from src.docs.healing_publisher import PublishResult

        return PublishResult(
            branch_name="auto-heal/docs/docs-test",
            commit_sha="abcdef",
            pr_url="https://github.com/example/repo/pull/1",
            skipped_reason=None,
        )

    monkeypatch.setattr(
        "src.docs.healing_publisher.publish_healing", fake_publish
    )

    args = argparse.Namespace(
        doc=str(repo / "docs" / "docs.md"),
        output=str(tmp_path / "out"),
        pr_base_branch="dev",
        pr_remote="origin",
        pr_dry_run=False,
    )

    main_module._maybe_publish_healing(
        report, args, logging.getLogger("test")
    )

    assert captured, "publish_healing was never called"
    assert captured["healing_report"] is healing
    assert captured["doc_test_report"] is report
    # html_report_path is Optional[Path] — just check it's not required to be None
    assert captured["config"].base_branch == "dev"
    assert captured["config"].dry_run is False
