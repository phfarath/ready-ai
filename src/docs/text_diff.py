"""
Textual Diff Engine — compare two documentation Markdown files and produce a
structured report of added, removed, and modified steps.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..agent.state import DocStepState
from ..docs.manifest import DocManifest

logger = logging.getLogger(__name__)


@dataclass
class TextDiffResult:
    """Result of comparing two docs."""
    added: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    modified: list[dict] = field(default_factory=list)

    @property
    def total_changes(self) -> int:
        return len(self.added) + len(self.removed) + len(self.modified)

    @property
    def has_changes(self) -> bool:
        return self.total_changes > 0

    def to_dict(self) -> dict:
        return {
            "added": self.added,
            "removed": self.removed,
            "modified": self.modified,
            "total_changes": self.total_changes,
            "has_changes": self.has_changes,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_markdown(self, baseline_manifest: Optional[DocManifest] = None, current_manifest: Optional[DocManifest] = None) -> str:
        """Render a human-readable Markdown changelog."""
        lines = []
        lines.append("# 📋 Documentation Changelog")
        lines.append("")

        if baseline_manifest and current_manifest:
            lines.append("## Version Comparison")
            lines.append("")
            if baseline_manifest.app_version and current_manifest.app_version:
                lines.append(f"- **From:** {baseline_manifest.app_version}")
                lines.append(f"- **To:** {current_manifest.app_version}")
            if baseline_manifest.git_commit and current_manifest.git_commit:
                lines.append(f"- **Commit Range:** `{baseline_manifest.git_commit[:7]}...{current_manifest.git_commit[:7]}`")
            if baseline_manifest.deployed_at and current_manifest.deployed_at:
                lines.append(f"- **Deployed:** {baseline_manifest.deployed_at} → {current_manifest.deployed_at}")
            lines.append("")

        lines.append("## Summary")
        lines.append("")
        lines.append(f"- **Added Steps:** {len(self.added)}")
        lines.append(f"- **Removed Steps:** {len(self.removed)}")
        lines.append(f"- **Modified Steps:** {len(self.modified)}")
        lines.append(f"- **Total Changes:** {self.total_changes}")
        lines.append("")

        if not self.has_changes:
            lines.append("> No textual changes detected in documentation. Screenshots may differ.")
            return "\n".join(lines)

        if self.added:
            lines.append("## ✅ Added Steps")
            lines.append("")
            for step in self.added:
                lines.append(f"### {step['title']}")
                lines.append("")
                lines.append(f"Number: {step['number']}")
                lines.append("")
                if step.get('annotation'):
                    lines.append(f"> **Annotation:** {step['annotation'][:200]}...")
                    lines.append("")
                lines.append("---")
                lines.append("")

        if self.removed:
            lines.append("## ❌ Removed Steps")
            lines.append("")
            for step in self.removed:
                lines.append(f"### {step['title']}")
                lines.append("")
                lines.append(f"Number: {step['number']}")
                lines.append("")
                lines.append("---")
                lines.append("")

        if self.modified:
            lines.append("## 📝 Modified Steps")
            lines.append("")
            for change in self.modified:
                step = change["step"]
                changes = change["changes"]
                lines.append(f"### {step['title']} (#{step['number']})")
                lines.append("")
                for field_name, diff in changes.items():
                    lines.append(f"#### {field_name}")
                    lines.append("")
                    lines.append("**Before:**")
                    lines.append(f"> {diff['before'][:300]}{'...' if len(diff['before']) > 300 else ''}")
                    lines.append("")
                    lines.append("**After:**")
                    lines.append(f"> {diff['after'][:300]}{'...' if len(diff['after']) > 300 else ''}")
                    lines.append("")
                lines.append("---")
                lines.append("")

        return "\n".join(lines)


def compare_docs(
    baseline_doc_path: str | Path,
    current_doc_path: str | Path,
) -> TextDiffResult:
    """
    Compare two documentation Markdown files and return a structured diff.
    """
    from src.docs.parser import parse_doc  # lazy import to avoid circular deps

    baseline_steps = parse_doc(baseline_doc_path)
    current_steps = parse_doc(current_doc_path)

    return compare_steps(baseline_steps, current_steps)


def compare_steps(
    baseline_steps: list[DocStepState],
    current_steps: list[DocStepState],
) -> TextDiffResult:
    """
    Compare two lists of DocStepState and identify changes.
    """
    result = TextDiffResult()

    # Index steps by number for O(1) lookup
    baseline_map = {s.number: s for s in baseline_steps}
    current_map = {s.number: s for s in current_steps}

    # Find added and removed
    baseline_nums = set(baseline_map)
    current_nums = set(current_map)

    removed_nums = baseline_nums - current_nums
    added_nums = current_nums - baseline_nums
    common_nums = baseline_nums & current_nums

    # Added steps
    for num in sorted(added_nums):
        step = current_map[num]
        result.added.append({
            "number": step.number,
            "title": step.title,
            "annotation": step.annotation,
            "action_description": step.action_description,
        })

    # Removed steps
    for num in sorted(removed_nums):
        step = baseline_map[num]
        result.removed.append({
            "number": step.number,
            "title": step.title,
        })

    # Modified steps
    for num in sorted(common_nums):
        baseline = baseline_map[num]
        current = current_map[num]

        changes = {}
        if baseline.title != current.title:
            changes["title"] = {"before": baseline.title, "after": current.title}
        if baseline.annotation != current.annotation:
            changes["annotation"] = {
                "before": baseline.annotation,
                "after": current.annotation,
            }
        if baseline.action_description != current.action_description:
            changes["action_description"] = {
                "before": baseline.action_description,
                "after": current.action_description,
            }
        if baseline.status != current.status:
            changes["status"] = {
                "before": baseline.status,
                "after": current.status,
            }

        if changes:
            result.modified.append({
                "step": {
                    "number": current.number,
                    "title": current.title,
                },
                "changes": changes,
            })

    return result
