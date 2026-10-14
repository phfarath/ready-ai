"""Packaging contract tests (READY-AI-T-15).

Static gates on ``pyproject.toml`` and the public package: version
consistency, PEP 561 ``py.typed``, distribution metadata (readme,
license, classifiers, project URLs) and a strict-mode mypy gate over
``ready_ai`` + ``examples`` — the public typing contract.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_pyproject() -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib
    return tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )


def test_version_matches_package():
    """``ready_ai.__version__`` must equal the wheel's declared version —
    a drifted constant breaks every downstream pin/debug."""
    import ready_ai

    project = _read_pyproject()["project"]
    assert ready_ai.__version__ == project["version"]


def test_py_typed_marker_present():
    assert (REPO_ROOT / "ready_ai" / "py.typed").is_file()
    package_data = (
        _read_pyproject()
        .get("tool", {})
        .get("setuptools", {})
        .get("package-data", {})
    )
    assert "py.typed" in package_data.get("ready_ai", []), (
        "setuptools must ship py.typed as ready_ai package data"
    )


def test_distribution_metadata_declared():
    project = _read_pyproject()["project"]
    assert project["readme"] == "README.md"
    assert (REPO_ROOT / project["readme"]).is_file()
    assert "license" in project
    classifiers = project.get("classifiers") or []
    assert "Typing :: Typed" in classifiers
    for version in ("3.10", "3.11", "3.12"):
        assert f"Programming Language :: Python :: {version}" in classifiers
    urls = project.get("urls") or {}
    for key in ("Repository", "Changelog"):
        assert urls.get(key, "").startswith("https://")


def test_runtime_deps_stay_minimal():
    """No broad transitive dependencies: runtime deps must stay the
    declared minimal set — new entries need explicit justification."""
    deps = _read_pyproject()["project"]["dependencies"]
    names = {
        dep.split(">=")[0].split("<")[0].split(";")[0].strip()
        for dep in deps
    }
    assert names == {
        "websockets",
        "litellm",
        "aiohttp",
        "Pillow",
        "python-dotenv",
        "PyYAML",
        "tomli",
    }


def test_mypy_gate_on_public_surface():
    """Strict mypy over ``ready_ai`` + ``examples`` — the public typing
    contract; legacy ``src`` internals are followed silently."""
    if importlib.util.find_spec("mypy") is None or shutil.which("mypy") is None:
        pytest.skip("mypy not installed (dev extra)")
    proc = subprocess.run(
        [sys.executable, "-m", "mypy"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"mypy failed:\n{proc.stdout}\n{proc.stderr}"


def test_examples_are_typechecked_surface():
    """The mypy gate must actually cover the examples dir — an empty
    ``files`` entry would pass vacuously."""
    mypy_files = _read_pyproject().get("tool", {}).get("mypy", {}).get(
        "files", []
    )
    assert "ready_ai" in mypy_files
    assert "examples" in mypy_files
    assert (REPO_ROOT / "examples" / "sdk_usage.py").is_file()
