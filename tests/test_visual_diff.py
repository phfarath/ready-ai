"""Tests for the visual diff engine (src/docs/visual_diff.py)."""

import pytest
from pathlib import Path
from PIL import Image
from src.docs.visual_diff import compare_screenshots


# ─── Helpers ────────────────────────────────────────────────────────────


def _create_solid_image(path: Path, color: tuple, size: tuple = (100, 100)):
    """Create a solid-colored test image."""
    img = Image.new("RGB", size, color)
    img.save(str(path), "PNG")
    return path


# ─── Tests ──────────────────────────────────────────────────────────────


def test_identical_images(tmp_path: Path):
    baseline = _create_solid_image(tmp_path / "baseline.png", (255, 0, 0))
    current = _create_solid_image(tmp_path / "current.png", (255, 0, 0))

    result = compare_screenshots(str(baseline), str(current))

    assert result.similarity_score == 1.0
    assert result.is_outdated is False
    assert result.diff_image_path is None


def test_completely_different_images(tmp_path: Path):
    baseline = _create_solid_image(tmp_path / "baseline.png", (0, 0, 0))
    current = _create_solid_image(tmp_path / "current.png", (255, 255, 255))

    result = compare_screenshots(str(baseline), str(current))

    assert result.similarity_score == 0.0
    assert result.is_outdated is True


def test_partially_different_images(tmp_path: Path):
    # Create a baseline image
    baseline_img = Image.new("RGB", (100, 100), (200, 200, 200))
    baseline_path = tmp_path / "baseline.png"
    baseline_img.save(str(baseline_path), "PNG")

    # Create current with a small change (10x10 red block in corner)
    current_img = Image.new("RGB", (100, 100), (200, 200, 200))
    for x in range(10):
        for y in range(10):
            current_img.putpixel((x, y), (255, 0, 0))
    current_path = tmp_path / "current.png"
    current_img.save(str(current_path), "PNG")

    result = compare_screenshots(str(baseline_path), str(current_path))

    # Should be mostly similar but not identical
    assert 0.5 < result.similarity_score < 1.0
    assert result.is_outdated is False  # small change within default threshold


def test_threshold_sensitivity(tmp_path: Path):
    baseline = _create_solid_image(tmp_path / "baseline.png", (200, 200, 200))
    current = _create_solid_image(tmp_path / "current.png", (180, 180, 180))

    # With high threshold, should detect drift
    result_strict = compare_screenshots(
        str(baseline), str(current), threshold=0.99
    )
    assert result_strict.is_outdated is True

    # With low threshold, should pass
    result_lenient = compare_screenshots(
        str(baseline), str(current), threshold=0.5
    )
    assert result_lenient.is_outdated is False


def test_diff_image_generated(tmp_path: Path):
    baseline = _create_solid_image(tmp_path / "baseline.png", (255, 0, 0))
    current = _create_solid_image(tmp_path / "current.png", (0, 0, 255))
    diff_path = tmp_path / "diff.png"

    result = compare_screenshots(
        str(baseline), str(current),
        output_diff_path=str(diff_path),
    )

    assert result.diff_image_path is not None
    assert Path(result.diff_image_path).exists()

    # Diff image should be wider (side-by-side: 3x width + margins)
    diff_img = Image.open(result.diff_image_path)
    assert diff_img.width > 100  # wider than original


def test_different_sizes_resized(tmp_path: Path):
    baseline = _create_solid_image(
        tmp_path / "baseline.png", (100, 100, 100), size=(200, 200)
    )
    current = _create_solid_image(
        tmp_path / "current.png", (100, 100, 100), size=(300, 300)
    )

    # Should not raise, current gets resized to match baseline
    result = compare_screenshots(str(baseline), str(current))
    assert result.similarity_score == 1.0


def test_nonexistent_baseline():
    with pytest.raises(FileNotFoundError):
        compare_screenshots("/nonexistent/baseline.png", "/nonexistent/current.png")


def test_diff_output_dir_created(tmp_path: Path):
    baseline = _create_solid_image(tmp_path / "baseline.png", (255, 0, 0))
    current = _create_solid_image(tmp_path / "current.png", (0, 255, 0))
    diff_path = tmp_path / "nested" / "deep" / "diff.png"

    result = compare_screenshots(
        str(baseline), str(current),
        output_diff_path=str(diff_path),
    )

    assert Path(result.diff_image_path).exists()
