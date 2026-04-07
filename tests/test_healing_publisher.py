"""
Unit tests for `src.docs.healing_publisher`.

All git/gh interaction goes through the injected `runner` callable so these
tests never touch a real repository.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.docs.auto_healer import HealingReport, HealResult
from src.docs.healing_publisher import (
    HealingPublishError,
    PublishConfig,
    _render_pr_body,
    publish_healing,
)


# ─── Fakes ────────────────────────────────────────────────────────────────


@dataclass
class FakeStepResult:
    step_number: int
    title: str


@dataclass
class FakeDocTestReport:
    overall_status: str = "DRIFT_DETECTED"
    results: list = field(default_factory=list)


class FakeRunner:
    """Records calls and returns scripted responses."""

    def __init__(self, responses: dict[tuple[str, ...], str] | None = None):
        self.calls: list[tuple[list[str], Path]] = []
        self.responses = responses or {}

    def __call__(self, cmd: list[str], cwd: Path) -> str:
        self.calls.append((list(cmd), cwd))
        key = tuple(cmd)
        # Exact match first
        if key in self.responses:
            return self.responses[key]
        # Prefix match (e.g., any `git status --porcelain`)
        for prefix, value in self.responses.items():
            if key[: len(prefix)] == prefix:
                return value
        return ""

    def commands(self) -> list[list[str]]:
        return [c for c, _ in self.calls]


# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "screenshots").mkdir(parents=True)
    (repo / "docs" / "docs.md").write_text("# doc\n", encoding="utf-8")
    (repo / "docs" / "screenshots" / "step_01.png").write_bytes(b"fake-png")
    (repo / "docs" / "screenshots" / "step_02.png").write_bytes(b"fake-png")
    return repo


@pytest.fixture
def config(repo: Path) -> PublishConfig:
    return PublishConfig(
        repo_root=repo,
        doc_path=repo / "docs" / "docs.md",
        base_branch="dev",
    )


@pytest.fixture
def healing_report() -> HealingReport:
    report = HealingReport(doc_rewritten=True)
    report.steps_healed = [
        HealResult(step_number=1, screenshot_updated=True, annotation_updated=True,
                   new_annotation="New annotation for step 1"),
        HealResult(step_number=2, screenshot_updated=True),
    ]
    return report


@pytest.fixture
def doc_test_report() -> FakeDocTestReport:
    return FakeDocTestReport(
        overall_status="DRIFT_DETECTED",
        results=[
            FakeStepResult(step_number=1, title="Click login button"),
            FakeStepResult(step_number=2, title="Fill password field"),
        ],
    )


FIXED_NOW = lambda: datetime(2026, 4, 7, 10, 0, 0, tzinfo=timezone.utc)  # noqa: E731


# ─── Tests ────────────────────────────────────────────────────────────────


def test_publish_no_changes_returns_noop(config, doc_test_report):
    empty = HealingReport()
    runner = FakeRunner()
    result = publish_healing(empty, doc_test_report, None, config, runner=runner)

    assert result.skipped_reason == "no-op"
    assert runner.calls == []


def test_publish_creates_branch_commits_and_opens_pr(
    config, healing_report, doc_test_report
):
    runner = FakeRunner(
        responses={
            ("git", "status", "--porcelain"): (
                " M docs/docs.md\n"
                " M docs/screenshots/step_01.png\n"
                " M docs/screenshots/step_02.png\n"
            ),
            ("git", "diff", "--cached", "--name-only"): "docs/docs.md\n",
            ("git", "rev-parse", "HEAD"): "deadbeefcafebabe\n",
            ("gh", "pr", "create"): "https://github.com/example/repo/pull/42\n",
        }
    )

    result = publish_healing(
        healing_report,
        doc_test_report,
        html_report_path=Path("healing-report.html"),
        config=config,
        runner=runner,
        now=FIXED_NOW,
    )

    cmds = runner.commands()
    # Branch name is deterministic with injected clock
    expected_branch = "auto-heal/docs/docs-20260407-100000"
    assert result.branch_name == expected_branch
    assert result.commit_sha == "deadbeefcafebabe"
    assert result.pr_url == "https://github.com/example/repo/pull/42"
    assert result.skipped_reason is None

    # Expected sequence of git operations
    assert ["git", "status", "--porcelain"] in cmds
    assert ["git", "fetch", "origin", "dev"] in cmds
    assert ["git", "checkout", "-B", expected_branch, "origin/dev"] in cmds

    # `git add` includes exactly the healed files (no -A, no stray paths)
    add_cmd = next(c for c in cmds if c[:2] == ["git", "add"])
    assert add_cmd[:3] == ["git", "add", "--"]
    staged = set(add_cmd[3:])
    assert staged == {
        "docs/docs.md",
        "docs/screenshots/step_01.png",
        "docs/screenshots/step_02.png",
    }

    assert any(c[:2] == ["git", "commit"] for c in cmds)
    assert ["git", "push", "-u", "origin", expected_branch] in cmds
    assert any(c[:3] == ["gh", "pr", "create"] for c in cmds)


def test_publish_dry_run_skips_push_and_pr(
    config, healing_report, doc_test_report
):
    config.dry_run = True
    runner = FakeRunner(
        responses={
            ("git", "status", "--porcelain"): (
                " M docs/docs.md\n"
                " M docs/screenshots/step_01.png\n"
                " M docs/screenshots/step_02.png\n"
            ),
            ("git", "diff", "--cached", "--name-only"): "docs/docs.md\n",
            ("git", "rev-parse", "HEAD"): "abc1234\n",
        }
    )

    result = publish_healing(
        healing_report, doc_test_report, None, config, runner=runner, now=FIXED_NOW
    )

    cmds = runner.commands()
    assert result.skipped_reason == "dry-run"
    assert result.pr_url is None
    assert result.commit_sha == "abc1234"
    assert not any(c[:2] == ["git", "push"] for c in cmds)
    assert not any(c[:1] == ["gh"] for c in cmds)
    # Branch+commit still happened locally
    assert any(c[:3] == ["git", "checkout", "-B"] for c in cmds)
    assert any(c[:2] == ["git", "commit"] for c in cmds)


def test_publish_aborts_on_dirty_unrelated_files(
    config, healing_report, doc_test_report
):
    runner = FakeRunner(
        responses={
            ("git", "status", "--porcelain"): (
                " M docs/docs.md\n"
                " M src/unrelated.py\n"
            ),
        }
    )

    with pytest.raises(HealingPublishError, match="unrelated changes"):
        publish_healing(
            healing_report, doc_test_report, None, config, runner=runner, now=FIXED_NOW
        )

    cmds = runner.commands()
    # No branch creation, commit, push, or PR
    assert not any(c[:2] == ["git", "fetch"] for c in cmds)
    assert not any(c[:2] == ["git", "commit"] for c in cmds)
    assert not any(c[:1] == ["gh"] for c in cmds)


def test_publish_skips_commit_when_nothing_staged(
    config, healing_report, doc_test_report
):
    """If the checkout -B brought the tree back to base, there's nothing to commit."""
    runner = FakeRunner(
        responses={
            ("git", "status", "--porcelain"): (
                " M docs/docs.md\n"
                " M docs/screenshots/step_01.png\n"
                " M docs/screenshots/step_02.png\n"
            ),
            ("git", "diff", "--cached", "--name-only"): "",  # nothing staged
        }
    )

    result = publish_healing(
        healing_report, doc_test_report, None, config, runner=runner, now=FIXED_NOW
    )

    cmds = runner.commands()
    assert result.commit_sha is None
    assert result.skipped_reason == "no-op"
    assert not any(c[:2] == ["git", "commit"] for c in cmds)
    # Must not push or open a PR when nothing was committed.
    assert not any(c[:2] == ["git", "push"] for c in cmds)
    assert not any(c[:1] == ["gh"] for c in cmds)


def test_render_pr_body_contains_step_summary(healing_report, doc_test_report):
    body = _render_pr_body(
        healing_report=healing_report,
        doc_test_report=doc_test_report,
        html_report_path=Path("healing-report.html"),
        doc_path=Path("docs/docs.md"),
    )

    assert "Self-Healing Documentation" in body
    assert "**Steps healed:** 2" in body
    assert "**Screenshots updated:** 2" in body
    assert "**Annotations regenerated:** 1" in body
    assert "**Selectors recovered:** 0" in body
    assert "DRIFT_DETECTED" in body
    assert "Click login button" in body
    assert "Fill password field" in body
    # Table has both step rows
    assert "| 1 |" in body
    assert "| 2 |" in body
    assert "healing-report.html" in body


def test_main_maybe_publish_healing_dry_run(
    tmp_path, repo, healing_report, doc_test_report, monkeypatch
):
    """Integration seam: `main._maybe_publish_healing` wires runner → publisher."""
    import argparse
    import subprocess

    import main as main_module

    # Initialize a real git repo so `_find_repo_root` succeeds.
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    # Fake report as produced by DocTestRunner.
    report = doc_test_report
    report.healing_report = healing_report

    # Capture publisher invocation without actually running git/gh.
    captured: dict = {}

    def fake_publish(**kwargs):
        captured.update(kwargs)
        from src.docs.healing_publisher import PublishResult
        return PublishResult(
            branch_name="auto-heal/docs/docs-test",
            commit_sha="abcdef",
            pr_url=None,
            skipped_reason="dry-run",
        )

    monkeypatch.setattr(
        "src.docs.healing_publisher.publish_healing", fake_publish
    )

    args = argparse.Namespace(
        doc=str(repo / "docs" / "docs.md"),
        output=str(tmp_path / "out"),
        pr_base_branch="dev",
        pr_remote="origin",
        pr_dry_run=True,
    )

    import logging
    main_module._maybe_publish_healing(report, args, logging.getLogger("test"))

    assert captured["healing_report"] is healing_report
    assert captured["config"].dry_run is True
    assert captured["config"].base_branch == "dev"
    assert captured["config"].doc_path == (repo / "docs" / "docs.md").resolve()
    assert captured["config"].repo_root == repo.resolve()


def test_run_wraps_missing_executable_in_publish_error(tmp_path):
    """Missing `git`/`gh` binary must be raised as HealingPublishError, not FileNotFoundError."""
    from src.docs.healing_publisher import _run

    with pytest.raises(HealingPublishError, match="command not found"):
        _run(["definitely-not-a-real-binary-xyz"], tmp_path)


def test_publish_propagates_gh_failure(
    config, healing_report, doc_test_report
):
    def failing_runner(cmd: list[str], cwd: Path) -> str:
        if cmd[:3] == ["gh", "pr", "create"]:
            raise HealingPublishError("gh pr create failed: auth required")
        if cmd[:3] == ["git", "status"]:
            return (
                " M docs/docs.md\n"
                " M docs/screenshots/step_01.png\n"
                " M docs/screenshots/step_02.png\n"
            )
        if cmd[:4] == ["git", "diff", "--cached", "--name-only"]:
            return "docs/docs.md\n"
        if cmd[:2] == ["git", "rev-parse"]:
            return "cafef00d\n"
        return ""

    with pytest.raises(HealingPublishError, match="gh pr create failed"):
        publish_healing(
            healing_report, doc_test_report, None, config,
            runner=failing_runner, now=FIXED_NOW,
        )
