"""
Documentation Output — writes markdown and screenshot files to disk.
"""

import base64
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def save_docs(
    markdown: str,
    screenshots: dict[str, str],
    output_dir: str,
    run_id: str = "local_run",
    goal: str = "",
    url: str = "",
    model: str = "",
    language: str = "en",
    app_version: str = "",
    git_commit: str = "",
    deployed_at: str = "",
    status: str = "FINISHED",
    error: str = "",
) -> str:
    """
    Save the generated documentation to disk.

    Args:
        markdown: Rendered markdown content
        screenshots: Dict mapping filename → base64 PNG data
        output_dir: Output directory path
        run_id: Run identifier
        goal: Documentation goal
        url: Target URL
        model: LLM model used
        language: Output language
        app_version: Application version
        git_commit: Git commit hash
        deployed_at: Deployment timestamp
        status: Run status
        error: Error message if failed

    Returns:
        Path to the saved markdown file
    """
    output_path = Path(output_dir)
    screenshots_dir = output_path / "screenshots"

    # Create directories
    output_path.mkdir(parents=True, exist_ok=True)
    screenshots_dir.mkdir(exist_ok=True)

    # Save screenshots as PNG files
    for filename, b64_data in screenshots.items():
        filepath = screenshots_dir / filename
        try:
            png_data = base64.b64decode(b64_data)
            filepath.write_bytes(png_data)
            logger.info(f"Saved screenshot: {filepath}")
        except Exception as e:
            logger.error(f"Failed to save screenshot {filename}: {e}")

    # Save markdown
    md_path = output_path / "docs.md"
    md_path.write_text(markdown, encoding="utf-8")
    logger.info(f"Saved documentation: {md_path}")

    # Save manifest
    from .manifest import create_manifest
    manifest = create_manifest(
        run_id=run_id,
        goal=goal,
        url=url,
        steps_count=len(screenshots),
        screenshots_count=len(screenshots),
        status=status,
        error=error,
        model=model,
        language=language,
        app_version=app_version,
        git_commit=git_commit,
        deployed_at=deployed_at,
    )
    manifest.to_file(output_path / "manifest.json")

    # Save a summary
    summary_path = output_path / "summary.txt"
    summary_path.write_text(
        f"Generated documentation\n"
        f"Steps: {len(screenshots)}\n"
        f"Screenshots: {list(screenshots.keys())}\n",
        encoding="utf-8",
    )

    return str(md_path)
