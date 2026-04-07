"""
Healing Publisher — commits auto-heal results to git and opens a PR.

This module is the "write-back" layer of self-healing documentation. After
`DocAutoHealer.heal_report()` has mutated `docs.md` and the screenshot files
locally, `publish_healing()` creates a branch, commits only the healed files,
pushes it, and opens a PR via `gh` CLI.

Design principles
-----------------
- **Pure I/O layer**: The healer stays unaware of git. This module consumes
  its output (`HealingReport`) and turns it into a revisable PR.
- **Conservative staging**: Only files referenced in the HealingReport are
  staged — never `git add -A`. If the working tree has unrelated changes,
  the publisher aborts to avoid hijacking the user's work.
- **Dry-run friendly**: `dry_run=True` runs the local git steps (branch,
  commit) without `push` or `gh pr create`, so developers can verify locally
  without credentials.
- **Subprocess isolation**: All git/gh commands go through `_run()` so tests
  can mock a single entrypoint.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from ..agent.test_runner import DocTestReport
    from .auto_healer import HealingReport

logger = logging.getLogger(__name__)


# ─── Data models ──────────────────────────────────────────────────────────


@dataclass
class PublishConfig:
    """Configuration for publishing a healing run."""

    repo_root: Path
    doc_path: Path
    base_branch: str = "dev"
    branch_prefix: str = "auto-heal/docs"
    remote: str = "origin"
    dry_run: bool = False
    commit_message_template: str = (
        "docs(self-heal): auto-update {doc_stem} ({count} step(s))"
    )
    pr_title_template: str = (
        "docs(self-heal): auto-heal for {doc_stem} ({count} step(s))"
    )


@dataclass
class PublishResult:
    """Outcome of a publish attempt."""

    branch_name: str = ""
    commit_sha: Optional[str] = None
    pr_url: Optional[str] = None
    files_changed: list[Path] = field(default_factory=list)
    skipped_reason: Optional[str] = None


class HealingPublishError(RuntimeError):
    """Raised when publishing cannot proceed safely."""


# ─── Subprocess helper (single mock point) ────────────────────────────────


Runner = Callable[[list[str], Path], str]


def _run(cmd: list[str], cwd: Path) -> str:
    """Execute a subprocess and return stdout; raise with stderr on failure."""
    logger.debug("publisher: running %s (cwd=%s)", cmd, cwd)
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        # Missing executable (e.g., `gh` not installed). Wrap so callers that
        # suppress HealingPublishError keep publishing as a pure side effect.
        raise HealingPublishError(
            f"command not found: {cmd[0]!r} (is it installed and on PATH?)"
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        raise HealingPublishError(
            f"command failed: {' '.join(cmd)}\nstdout: {stdout}\nstderr: {stderr}"
        ) from exc
    return result.stdout


# ─── Core logic ───────────────────────────────────────────────────────────


def publish_healing(
    healing_report: "HealingReport",
    doc_test_report: "DocTestReport",
    html_report_path: Optional[Path],
    config: PublishConfig,
    *,
    runner: Runner = _run,
    now: Optional[Callable[[], datetime]] = None,
) -> PublishResult:
    """
    Commit healed files to a new branch and open a PR.

    Parameters
    ----------
    healing_report:
        Output of `DocAutoHealer.heal_report()` — drives which files get staged.
    doc_test_report:
        Full doc-test report (used to enrich the PR body).
    html_report_path:
        Optional path to a standalone HTML report; mentioned in PR body when
        provided so CI artifact links are discoverable.
    config:
        Publishing configuration.
    runner:
        Subprocess runner; injected for tests.
    now:
        Clock function; injected for tests (defaults to UTC now).
    """
    if healing_report.total_healed == 0:
        logger.info("publisher: nothing to publish (total_healed=0)")
        return PublishResult(skipped_reason="no-op")

    now_fn = now or (lambda: datetime.now(timezone.utc))
    repo = config.repo_root.resolve()
    doc_path = config.doc_path.resolve()

    files_changed = _collect_healed_files(healing_report, doc_path)
    if not files_changed:
        logger.info("publisher: no files to stage (empty allowlist)")
        return PublishResult(skipped_reason="no-op")

    allowlist = {_rel(repo, p) for p in files_changed}
    _ensure_clean_or_only_allowlisted(repo, allowlist, runner)

    branch_name = _build_branch_name(config, doc_path, now_fn())
    _create_branch(repo, config, branch_name, runner)

    commit_sha = _stage_and_commit(
        repo=repo,
        files=files_changed,
        message=config.commit_message_template.format(
            doc_stem=doc_path.stem,
            count=healing_report.total_healed,
        ),
        runner=runner,
    )

    if commit_sha is None:
        # Nothing changed relative to the base branch — pushing would yield an
        # empty branch and `gh pr create` would error with "no commits between".
        logger.info(
            "publisher: no commit created (healed files match base branch) — "
            "skipping push and PR"
        )
        return PublishResult(
            branch_name=branch_name,
            commit_sha=None,
            pr_url=None,
            files_changed=files_changed,
            skipped_reason="no-op",
        )

    if config.dry_run:
        logger.info("publisher: dry-run enabled — skipping push and PR creation")
        return PublishResult(
            branch_name=branch_name,
            commit_sha=commit_sha,
            pr_url=None,
            files_changed=files_changed,
            skipped_reason="dry-run",
        )

    _push(repo, config.remote, branch_name, runner)

    pr_title = config.pr_title_template.format(
        doc_stem=doc_path.stem,
        count=healing_report.total_healed,
    )
    pr_body = _render_pr_body(
        healing_report=healing_report,
        doc_test_report=doc_test_report,
        html_report_path=html_report_path,
        doc_path=doc_path,
    )
    pr_url = _open_pr(
        repo=repo,
        title=pr_title,
        body=pr_body,
        base=config.base_branch,
        head=branch_name,
        runner=runner,
    )

    return PublishResult(
        branch_name=branch_name,
        commit_sha=commit_sha,
        pr_url=pr_url,
        files_changed=files_changed,
        skipped_reason=None,
    )


# ─── Internals ────────────────────────────────────────────────────────────


def _rel(repo: Path, target: Path) -> str:
    """Return `target` relative to `repo` as a POSIX string."""
    try:
        return target.resolve().relative_to(repo).as_posix()
    except ValueError as exc:
        raise HealingPublishError(
            f"path {target} is outside repo root {repo}"
        ) from exc


def _collect_healed_files(
    healing_report: "HealingReport", doc_path: Path
) -> list[Path]:
    """Build the list of files that should be staged."""
    files: list[Path] = []
    if healing_report.doc_rewritten:
        files.append(doc_path)

    screenshots_dir = doc_path.parent / "screenshots"
    for heal in healing_report.steps_healed:
        if heal.screenshot_updated:
            shot = screenshots_dir / f"step_{heal.step_number:02d}.png"
            files.append(shot)

    # De-duplicate preserving order
    seen: set[Path] = set()
    unique: list[Path] = []
    for f in files:
        resolved = f.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _ensure_clean_or_only_allowlisted(
    repo: Path, allowlist: set[str], runner: Runner
) -> None:
    """Abort if the working tree has changes outside the allowlist."""
    output = runner(["git", "status", "--porcelain"], repo)
    offending: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        # porcelain format: "XY path" — path starts at column 3
        path = line[3:].strip()
        # Handle rename: "old -> new"
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path not in allowlist:
            offending.append(path)
    if offending:
        raise HealingPublishError(
            "working tree has unrelated changes; refusing to publish. "
            f"Offending paths: {offending}"
        )


def _build_branch_name(
    config: PublishConfig, doc_path: Path, now: datetime
) -> str:
    stamp = now.strftime("%Y%m%d-%H%M%S")
    return f"{config.branch_prefix}/{doc_path.stem}-{stamp}"


def _create_branch(
    repo: Path, config: PublishConfig, branch_name: str, runner: Runner
) -> None:
    runner(["git", "fetch", config.remote, config.base_branch], repo)
    runner(
        [
            "git",
            "checkout",
            "-B",
            branch_name,
            f"{config.remote}/{config.base_branch}",
        ],
        repo,
    )


def _stage_and_commit(
    repo: Path, files: list[Path], message: str, runner: Runner
) -> Optional[str]:
    rel_files = [_rel(repo, f) for f in files]
    runner(["git", "add", "--", *rel_files], repo)

    # Detect whether there is anything staged (the checkout -B may have brought
    # the tree to a state where the healed files match the base branch).
    diff = runner(["git", "diff", "--cached", "--name-only"], repo)
    if not diff.strip():
        logger.info("publisher: nothing staged after add — skipping commit")
        return None

    runner(["git", "commit", "-m", message], repo)
    sha = runner(["git", "rev-parse", "HEAD"], repo).strip()
    return sha or None


def _push(repo: Path, remote: str, branch_name: str, runner: Runner) -> None:
    runner(["git", "push", "-u", remote, branch_name], repo)


def _open_pr(
    repo: Path,
    title: str,
    body: str,
    base: str,
    head: str,
    runner: Runner,
) -> Optional[str]:
    output = runner(
        [
            "gh",
            "pr",
            "create",
            "--title",
            title,
            "--body",
            body,
            "--base",
            base,
            "--head",
            head,
        ],
        repo,
    )
    url = output.strip().splitlines()[-1] if output.strip() else ""
    return url or None


# ─── PR body rendering ────────────────────────────────────────────────────


def _render_pr_body(
    *,
    healing_report: "HealingReport",
    doc_test_report: "DocTestReport",
    html_report_path: Optional[Path],
    doc_path: Path,
) -> str:
    lines: list[str] = []
    lines.append("## Self-Healing Documentation — Auto PR")
    lines.append("")
    lines.append(
        f"This PR was generated automatically by the self-healing pipeline "
        f"after detecting UI drift in `{doc_path.name}`."
    )
    lines.append("")

    # Summary counters
    total = healing_report.total_healed
    screenshots = sum(1 for r in healing_report.steps_healed if r.screenshot_updated)
    annotations = sum(1 for r in healing_report.steps_healed if r.annotation_updated)
    selectors = sum(1 for r in healing_report.steps_healed if r.selector_recovered)
    lines.append("### Summary")
    lines.append("")
    lines.append(f"- **Steps healed:** {total}")
    lines.append(f"- **Screenshots updated:** {screenshots}")
    lines.append(f"- **Annotations regenerated:** {annotations}")
    lines.append(f"- **Selectors recovered:** {selectors}")
    lines.append(
        f"- **Overall doc-test status:** `{getattr(doc_test_report, 'overall_status', 'UNKNOWN')}`"
    )
    lines.append("")

    # Per-step table
    lines.append("### Healed steps")
    lines.append("")
    lines.append("| Step | Title | Changes | Notes |")
    lines.append("| ---- | ----- | ------- | ----- |")
    step_titles = {
        getattr(r, "step_number", None): getattr(r, "title", "")
        for r in getattr(doc_test_report, "results", [])
    }
    for heal in healing_report.steps_healed:
        changes: list[str] = []
        if heal.screenshot_updated:
            changes.append("screenshot")
        if heal.annotation_updated:
            changes.append("annotation")
        if heal.selector_recovered:
            changes.append("selector")
        if not changes:
            continue
        title = step_titles.get(heal.step_number, "")
        notes = _truncate(heal.new_annotation or heal.new_selector or heal.error, 120)
        lines.append(
            f"| {heal.step_number} | {_escape_pipe(title)} | "
            f"{', '.join(changes)} | {_escape_pipe(notes)} |"
        )
    lines.append("")

    if html_report_path is not None:
        lines.append(
            f"A standalone HTML report was generated at "
            f"`{html_report_path.name}` and is available as a CI artifact."
        )
        lines.append("")

    lines.append("---")
    lines.append(
        "**Automated PR — please review the visual diffs before merging.** "
        "If any step was healed incorrectly, close this PR and re-run the "
        "test runner with a stricter threshold or disable `--auto-heal`."
    )
    return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _escape_pipe(text: str) -> str:
    return (text or "").replace("|", "\\|")
