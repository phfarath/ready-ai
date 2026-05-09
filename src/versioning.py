"""
Versioning helpers — resolve app_version, git_commit, deployed_at from env/args.
"""

import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def resolve_app_version(explicit: Optional[str] = None) -> str:
    """Resolve application version from env or explicit arg."""
    if explicit:
        return explicit.strip()

    # Common CI/CD env vars
    for env_var in ("APP_VERSION", "RELEASE_VERSION", "TAG_NAME", "COMMIT_TAG", "IMAGE_TAG"):
        value = os.environ.get(env_var, "").strip()
        if value and value != "$TAG_NAME":  # unexpanded GitHub var
            logger.debug(f"Resolved app_version from {env_var}={value}")
            return value

    # Try git tag of current commit
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            cwd=os.getcwd(),
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip().lstrip("v")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    logger.warning("app_version not found — defaulting to '0.0.0'")
    return "0.0.0"


def resolve_git_commit(explicit: Optional[str] = None) -> str:
    """Resolve git commit hash from env or explicit arg."""
    if explicit:
        return explicit.strip()

    for env_var in ("GIT_COMMIT", "GITHUB_SHA", "CI_COMMIT_SHA", "CIRCLE_SHA1", "BUILDKITE_COMMIT"):
        value = os.environ.get(env_var, "").strip()
        if value and len(value) >= 7:
            return value

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=os.getcwd(),
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return ""


def resolve_deployed_at(explicit: Optional[str] = None) -> str:
    """Resolve deployment timestamp."""
    if explicit:
        return explicit.strip()

    # Try env var
    env_val = os.environ.get("DEPLOYED_AT", "").strip()
    if env_val:
        return env_val

    # Default to now (UTC)
    return datetime.now(timezone.utc).isoformat()


def get_versioning_context(
    app_version: Optional[str] = None,
    git_commit: Optional[str] = None,
    deployed_at: Optional[str] = None,
) -> dict:
    """Resolve all versioning fields into a dict."""
    return {
        "app_version": resolve_app_version(app_version),
        "git_commit": resolve_git_commit(git_commit),
        "deployed_at": resolve_deployed_at(deployed_at),
    }
