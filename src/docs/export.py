"""
Documentation-as-Code Exporter.

Converts ready-ai generated docs (docs.md + screenshots) into
static-site friendly formats: Docusaurus, Nextra, Mintlify, Starlight.

Usage:
    from src.docs.export import export_docs
    export_docs(
        doc_path="./output/run/docs.md",
        format="docusaurus",
        output_dir="./apps/docs/",
    )
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.docs.parser import extract_goal

logger = logging.getLogger(__name__)


# ─── Data models ──────────────────────────────────────────────────────────


@dataclass
class ExportResult:
    """Result of a doc export."""

    output_dir: Path
    files_created: list[Path]
    format: str

    @property
    def success(self) -> bool:
        """Convenience property for backward compatibility."""
        return True


# ─── Format registry ─────────────────────────────────────────────────────


Exporter = Callable[[Path, Path, Path], list[Path]]

_REGISTRY: dict[str, Exporter] = {}


def register(name: str):
    """Decorator to register an exporter by name."""
    def decorator(fn: Exporter) -> Exporter:
        _REGISTRY[name] = fn
        return fn
    return decorator


# ─── Common helpers ────────────────────────────────────────────────────────


def _copy_screenshots(screenshots_dir: Path, dest_dir: Path) -> list[Path]:
    """Copy all PNG files to the destination, return list of created paths."""
    created: list[Path] = []
    if not screenshots_dir.exists():
        return created
    dest_dir.mkdir(parents=True, exist_ok=True)
    for png in screenshots_dir.glob("*.png"):
        dest = dest_dir / png.name
        shutil.copy2(png, dest)
        created.append(dest)
    return created


def _slugify(title: str) -> str:
    """Convert a title to a URL-friendly slug."""
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")
    return slug


def _strip_frontmatter_and_h1(content: str) -> str:
    """Remove any existing YAML frontmatter and the first H1 heading."""
    # Strip YAML frontmatter ---\n...\n---
    content = re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, flags=re.DOTALL)
    # Strip first H1 heading
    content = re.sub(r"^#\s+.+\n", "", content)
    return content.strip()


def _rewrite_screenshot_paths(content: str, new_prefix: str = "./screenshots") -> str:
    """Rewrite all screenshot image paths to use the given prefix."""
    def _replacer(match: re.Match) -> str:
        alt = match.group(1)
        old_path = match.group(2)
        filename = Path(old_path).name
        return f"![{alt}]({new_prefix}/{filename})"

    pattern = re.compile(r"!\[(.*?)\]\(\.?/?screenshots/([^)]+)\)")
    return pattern.sub(_replacer, content)


def _extract_title(doc_path: Path) -> str:
    """Extract the H1 title from a docs.md file."""
    goal = extract_goal(doc_path)
    return goal or "Documentation"


# ─── Markdown exporter (default) ──────────────────────────────────────────


@register("markdown")
def _export_markdown(doc_path: Path, output_dir: Path, screenshots_src: Path) -> list[Path]:
    """Plain markdown — just copy docs.md and screenshots with path fixing."""
    output_dir.mkdir(parents=True, exist_ok=True)

    content = doc_path.read_text(encoding="utf-8")
    # For flat markdown, keep paths relative but ensure ./ prefix
    content = _rewrite_screenshot_paths(content, "./screenshots")

    dest_doc = output_dir / doc_path.name
    dest_doc.write_text(content, encoding="utf-8")

    created = [dest_doc]
    created += _copy_screenshots(screenshots_src, output_dir / "screenshots")
    return created


# ─── Docusaurus exporter ──────────────────────────────────────────────────


@register("docusaurus")
def _export_docusaurus(doc_path: Path, output_dir: Path, screenshots_src: Path) -> list[Path]:
    """
    Docusaurus format:
    - docs/
      - {slug}.md  (frontmatter with slug + sidebar_position)
      - screenshots/
    """
    docs_dir = output_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    title = _extract_title(doc_path)
    slug = _slugify(title)

    content = doc_path.read_text(encoding="utf-8")
    content = _strip_frontmatter_and_h1(content)
    content = _rewrite_screenshot_paths(content, "./screenshots")

    frontmatter = f"""---
sidebar_position: 1
slug: /{slug}
---

# {title}

"""

    dest = docs_dir / f"{slug}.md"
    dest.write_text(frontmatter + content, encoding="utf-8")

    created = [dest]
    created += _copy_screenshots(screenshots_src, docs_dir / "screenshots")
    return created


# ─── Nextra exporter ────────────────────────────────────────────────────


@register("nextra")
def _export_nextra(doc_path: Path, output_dir: Path, screenshots_src: Path) -> list[Path]:
    """
    Nextra format:
    - pages/
      - docs/
        - index.mdx  (MDX with frontmatter)
        - screenshots/
    """
    pages_dir = output_dir / "pages" / "docs"
    pages_dir.mkdir(parents=True, exist_ok=True)

    title = _extract_title(doc_path)

    content = doc_path.read_text(encoding="utf-8")
    content = _strip_frontmatter_and_h1(content)
    content = _rewrite_screenshot_paths(content, "./screenshots")

    frontmatter = f"""export const metadata = {{
  title: '{title}',
}}

# {title}

"""

    dest = pages_dir / "index.mdx"
    dest.write_text(frontmatter + content, encoding="utf-8")

    created = [dest]
    created += _copy_screenshots(screenshots_src, pages_dir / "screenshots")
    return created


# ─── Mintlify exporter ──────────────────────────────────────────────────


@register("mintlify")
def _export_mintlify(doc_path: Path, output_dir: Path, screenshots_src: Path) -> list[Path]:
    """
    Mintlify format:
    - uses mint.json for navigation
    - pages are plain .mdx in root or subdirs
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    title = _extract_title(doc_path)

    content = doc_path.read_text(encoding="utf-8")
    content = _strip_frontmatter_and_h1(content)
    content = _rewrite_screenshot_paths(content, "./screenshots")

    frontmatter = f"""---
title: "{title}"
description: "Auto-generated documentation"
---

"""

    dest = output_dir / "overview.mdx"
    dest.write_text(frontmatter + content, encoding="utf-8")

    created = [dest]
    created += _copy_screenshots(screenshots_src, output_dir / "screenshots")
    return created


# ─── Starlight (Astro) exporter ───────────────────────────────────────────


@register("starlight")
def _export_starlight(doc_path: Path, output_dir: Path, screenshots_src: Path) -> list[Path]:
    """
    Astro Starlight format:
    - src/content/docs/
      - index.md  (with Astro frontmatter)
      - screenshots/
    """
    content_dir = output_dir / "src" / "content" / "docs"
    content_dir.mkdir(parents=True, exist_ok=True)

    title = _extract_title(doc_path)

    content = doc_path.read_text(encoding="utf-8")
    content = _strip_frontmatter_and_h1(content)
    content = _rewrite_screenshot_paths(content, "./screenshots")

    frontmatter = f"""---
title: {title}
description: Auto-generated documentation
tableOfContents:
  minHeadingLevel: 2
  maxHeadingLevel: 3
---

"""

    dest = content_dir / "index.md"
    dest.write_text(frontmatter + content, encoding="utf-8")

    created = [dest]
    created += _copy_screenshots(screenshots_src, content_dir / "screenshots")
    return created


# ─── Public API ─────────────────────────────────────────────────────────


SUPPORTED_FORMATS = list(_REGISTRY.keys())


def export_docs(
    doc_path: str | Path,
    format: str,
    output_dir: str | Path,
    screenshots_dir: str | Path | None = None,
) -> ExportResult:
    """
    Export ready-ai documentation to a static-site format.

    Args:
        doc_path: Path to the generated docs.md
        format: One of 'markdown', 'docusaurus', 'nextra', 'mintlify', 'starlight'
        output_dir: Destination directory for the exported files
        screenshots_dir: Source directory for screenshots (defaults to doc_path/../screenshots)
    """
    doc_path = Path(doc_path)
    output_dir = Path(output_dir)

    if not doc_path.exists():
        raise FileNotFoundError(f"Documentation not found: {doc_path}")

    if format not in _REGISTRY:
        raise ValueError(
            f"Unknown format '{format}'. Supported: {', '.join(SUPPORTED_FORMATS)}"
        )

    if screenshots_dir is None:
        screenshots_dir = doc_path.parent / "screenshots"
    else:
        screenshots_dir = Path(screenshots_dir)

    exporter = _REGISTRY[format]
    files = exporter(doc_path, output_dir, screenshots_dir)

    logger.info(
        f"Exported {doc_path.name} to {format} format: "
        f"{len(files)} file(s) in {output_dir}"
    )

    return ExportResult(
        output_dir=output_dir,
        files_created=files,
        format=format,
    )


def export_batch(
    run_dirs: list[Path],
    format: str,
    output_dir: str | Path,
) -> list[ExportResult]:
    """
    Export multiple documentation runs into a single static-site output.

    Each run's docs.md is exported as a separate page with auto-incrementing
    sidebar_position (for Docusaurus) or a sub-page (for Nextra).

    Args:
        run_dirs: List of directories each containing docs.md + screenshots/
        format: Target export format
        output_dir: Destination directory

    Returns:
        List of ExportResult, one per run.
    """
    results: list[ExportResult] = []
    for idx, run_dir in enumerate(run_dirs):
        doc_path = run_dir / "docs.md"
        if not doc_path.exists():
            logger.warning(f"Skipping {run_dir}: docs.md not found")
            continue

        screenshots_dir = run_dir / "screenshots"

        if format == "docusaurus":
            # Each doc gets its own file with incremental sidebar_position
            results.append(
                _export_single_docusaurus(doc_path, Path(output_dir), screenshots_dir, idx + 1)
            )
        elif format == "nextra":
            # Each doc gets its own page under pages/docs/
            results.append(
                _export_single_nextra(doc_path, Path(output_dir), screenshots_dir, idx)
            )
        else:
            # Fallback: individual export into subdirectories
            sub_out = Path(output_dir) / f"run_{idx + 1}"
            results.append(export_docs(doc_path, format, sub_out, screenshots_dir))

    logger.info(f"Batch export complete: {len(results)} runs exported to {format}")
    return results


def _export_single_docusaurus(
    doc_path: Path, output_dir: Path, screenshots_src: Path, position: int
) -> ExportResult:
    """Export a single doc into a multi-doc Docusaurus site."""
    docs_dir = output_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    title = _extract_title(doc_path)
    slug = _slugify(title)

    content = doc_path.read_text(encoding="utf-8")
    content = _strip_frontmatter_and_h1(content)
    content = _rewrite_screenshot_paths(content, f"./{slug}/screenshots")

    frontmatter = f"""---
sidebar_position: {position}
slug: /{slug}
---

# {title}

"""

    dest = docs_dir / f"{slug}.md"
    dest.write_text(frontmatter + content, encoding="utf-8")

    # Screenshots go to docs/{slug}/screenshots to avoid collisions
    screenshots_dest = docs_dir / slug / "screenshots"
    screenshots_dest.mkdir(parents=True, exist_ok=True)
    files = [dest]
    files += _copy_screenshots(screenshots_src, screenshots_dest)

    return ExportResult(output_dir=output_dir, files_created=files, format="docusaurus")


def _export_single_nextra(
    doc_path: Path, output_dir: Path, screenshots_src: Path, idx: int
) -> ExportResult:
    """Export a single doc into a multi-doc Nextra site."""
    pages_dir = output_dir / "pages" / "docs"
    pages_dir.mkdir(parents=True, exist_ok=True)

    title = _extract_title(doc_path)
    slug = _slugify(title)
    filename = f"{slug}.mdx" if idx > 0 else "index.mdx"

    content = doc_path.read_text(encoding="utf-8")
    content = _strip_frontmatter_and_h1(content)
    content = _rewrite_screenshot_paths(content, "./screenshots")

    frontmatter = f"""export const metadata = {{
  title: '{title}',
}}

# {title}

"""

    dest = pages_dir / filename
    dest.write_text(frontmatter + content, encoding="utf-8")

    # Nextra: screenshots under pages/docs/screenshots/ (shared, no collision handling)
    files = [dest]
    files += _copy_screenshots(screenshots_src, pages_dir / "screenshots")

    return ExportResult(output_dir=output_dir, files_created=files, format="nextra")
