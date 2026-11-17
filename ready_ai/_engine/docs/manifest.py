"""
Manifest — writes a machine-readable manifest alongside every docs run.

The manifest tracks metadata about a documentation run so external tools
(or the test runner) can inspect it without parsing the Markdown.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..versioning import get_versioning_context

logger = logging.getLogger(__name__)


@dataclass
class DocManifest:
    """Metadata about a single documentation run."""

    run_id: str
    goal: str
    url: str
    app_version: str = ""
    git_commit: str = ""
    deployed_at: str = ""
    generated_at: str = ""
    language: str = "en"
    steps_count: int = 0
    screenshots_count: int = 0
    status: str = "FINISHED"  # FINISHED | FAILED | PARTIAL
    error: str = ""
    model: str = ""

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "url": self.url,
            "app_version": self.app_version,
            "git_commit": self.git_commit,
            "deployed_at": self.deployed_at,
            "generated_at": self.generated_at,
            "language": self.language,
            "steps_count": self.steps_count,
            "screenshots_count": self.screenshots_count,
            "status": self.status,
            "error": self.error,
            "model": self.model,
        }

    def to_file(self, path: str | Path) -> None:
        out = Path(path)
        out.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        logger.info(f"Manifest saved: {out}")

    @classmethod
    def from_file(cls, path: str | Path) -> Optional["DocManifest"]:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls(**data)
        except Exception:
            return None


def create_manifest(
    run_id: str,
    goal: str,
    url: str,
    steps_count: int = 0,
    screenshots_count: int = 0,
    status: str = "FINISHED",
    error: str = "",
    model: str = "",
    language: str = "en",
    app_version: Optional[str] = None,
    git_commit: Optional[str] = None,
    deployed_at: Optional[str] = None,
) -> DocManifest:
    """Create a DocManifest with auto-resolved versioning fields."""
    ctx = get_versioning_context(
        app_version=app_version,
        git_commit=git_commit,
        deployed_at=deployed_at,
    )
    return DocManifest(
        run_id=run_id,
        goal=goal,
        url=url,
        steps_count=steps_count,
        screenshots_count=screenshots_count,
        status=status,
        error=error,
        model=model,
        language=language,
        generated_at=datetime.now(timezone.utc).isoformat(),
        **ctx,
    )
