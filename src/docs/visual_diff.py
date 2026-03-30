"""
Visual Diff Engine — compares screenshots to detect UI drift.

Uses Pillow (already a project dependency) for pixel-level comparison
and diff image generation.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageChops

logger = logging.getLogger(__name__)


@dataclass
class DiffResult:
    """Result of comparing two screenshots."""
    similarity_score: float    # 0.0 = completely different, 1.0 = identical
    diff_image_path: Optional[str]  # path to generated diff image (if requested)
    is_outdated: bool          # True if similarity_score < threshold


def compare_screenshots(
    baseline_path: str | Path,
    current_path: str | Path,
    output_diff_path: Optional[str | Path] = None,
    threshold: float = 0.85,
) -> DiffResult:
    """
    Compare two screenshots and compute a similarity score.

    The score is based on normalized pixel-level difference: each pixel's
    RGB channels are compared, and the mean absolute difference across all
    channels is subtracted from 1.0 to produce a similarity score.

    Args:
        baseline_path: Path to the original/baseline screenshot.
        current_path: Path to the current/new screenshot.
        output_diff_path: If provided, saves a visual diff image highlighting changes.
        threshold: Similarity threshold below which the step is considered outdated.

    Returns:
        DiffResult with similarity score and outdated flag.

    Raises:
        FileNotFoundError: If either screenshot file does not exist.
    """
    baseline = Image.open(baseline_path).convert("RGB")
    current = Image.open(current_path).convert("RGB")

    # Resize current to match baseline dimensions if they differ
    if baseline.size != current.size:
        current = current.resize(baseline.size, Image.Resampling.LANCZOS)

    # Compute pixel-level difference
    diff = ImageChops.difference(baseline, current)

    # Calculate similarity score: 1.0 - (mean absolute difference / 255)
    _getdata = getattr(diff, "get_flattened_data", None) or diff.getdata
    diff_data = list(_getdata())
    total_channels = len(diff_data) * 3  # RGB
    if total_channels == 0:
        similarity = 1.0
    else:
        total_diff = sum(r + g + b for r, g, b in diff_data)
        similarity = 1.0 - (total_diff / (total_channels * 255))

    # Generate diff image if requested
    saved_diff_path: Optional[str] = None
    if output_diff_path:
        saved_diff_path = _generate_diff_image(
            baseline, current, diff, output_diff_path
        )

    is_outdated = similarity < threshold

    if is_outdated:
        logger.warning(
            f"Visual drift detected: similarity={similarity:.3f} "
            f"(threshold={threshold})"
        )
    else:
        logger.debug(f"Visual check passed: similarity={similarity:.3f}")

    return DiffResult(
        similarity_score=similarity,
        diff_image_path=saved_diff_path,
        is_outdated=is_outdated,
    )


def _generate_diff_image(
    baseline: Image.Image,
    current: Image.Image,
    diff: Image.Image,
    output_path: str | Path,
) -> str:
    """
    Generate a side-by-side diff image with changed areas highlighted in red.

    Layout: [baseline] | [current] | [diff highlighted]
    """
    width, height = baseline.size

    # Amplify the diff for visibility and tint it red
    highlighted = Image.new("RGB", (width, height), (0, 0, 0))
    diff_pixels = diff.load()
    high_pixels = highlighted.load()

    for y in range(height):
        for x in range(width):
            r, g, b = diff_pixels[x, y]
            intensity = max(r, g, b)
            if intensity > 10:  # threshold for noise
                # Highlight changes in red with amplified intensity
                high_pixels[x, y] = (min(255, intensity * 3), 0, 0)

    # Create side-by-side: baseline | current | diff
    margin = 4
    combined_width = width * 3 + margin * 2
    combined = Image.new("RGB", (combined_width, height), (40, 40, 40))
    combined.paste(baseline, (0, 0))
    combined.paste(current, (width + margin, 0))
    combined.paste(highlighted, (width * 2 + margin * 2, 0))

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    combined.save(str(out), "PNG")
    logger.info(f"Diff image saved: {out}")
    return str(out)
