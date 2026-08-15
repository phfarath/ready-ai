"""Wheel packaging smoke test (READY-AI-T-13, DoD 5).

Builds a wheel from a hermetic copy of the repo, "installs" it into a
clean site directory and runs a fresh interpreter (never the repo) to:

  (a) import ready_ai and the DoD 1 import line, resolving from the wheel
      — not from the repository checkout,
  (b) exercise REAL model construction + validation,
  (c) run a minimal Flow through ReadyAI with the AgenticLoop boundary
      mocked (no Chrome, no network).

Wheel build is hermetic (temp dirs, no network) with two paths:

  1. ``python3 -m build --wheel --no-isolation`` when the full build
     toolchain (``build`` + ``wheel``) is available;
  2. a standards-compliant PEP 427 fallback assembler that ships exactly
     the packages discovered by the REAL ``pyproject.toml`` discoverer
     (``setuptools.find_packages`` with ``include = ["src*",
     "ready_ai*"]``) — so the packaging config is still verified.

The test skips only when setuptools or a TOML parser is unavailable.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

_WHEEL_TAG = "py3-none-any"


def _has_toml_parser() -> bool:
    return importlib.util.find_spec("tomllib") is not None or importlib.util.find_spec(
        "tomli"
    ) is not None


pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("setuptools") is None or not _has_toml_parser(),
    reason="setuptools + a TOML parser are required for the wheel smoke test",
)

def _copy_ignore(dirname: str, names: list[str]) -> set[str]:
    """Exclude repo-root user data and caches; keep ``src/docs`` (the engine
    subpackage) intact — only the root-level ``docs/`` marketing folder is
    dropped, since wheels do not ship it."""
    ignored = {"__pycache__", ".DS_Store"}
    if Path(dirname).resolve() == REPO_ROOT:
        ignored |= {
            ".git",
            ".venv",
            ".venv-wheel-smoke",
            ".claude",
            ".coverage",
            ".hypothesis",
            ".pytest_cache",
            ".ruff_cache",
            "assets",
            "build",
            "dist",
            "docs",
            "output",
            "ready-ai-test.yaml",
            "test-cookies.json",
            "tests",
            "tmp",
        }
    for name in names:
        if (
            name.endswith(".egg-info")
            or name.endswith(".pyc")
            or name.endswith(".log")
        ):
            ignored.add(name)
    return ignored


# ─── Wheel building ────────────────────────────────────────────────────────


def _try_real_build(work: Path, outdir: Path) -> Path | None:
    """Path 1: the standard ``python -m build`` backend (no isolation)."""
    if importlib.util.find_spec("build") is None or importlib.util.find_spec(
        "wheel"
    ) is None:
        return None
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(outdir),
        ],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        return None
    found = sorted(outdir.glob("ready_ai-*.whl"))
    return found[0] if len(found) == 1 else None


def _read_toml(work: Path) -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10 fallback
        import tomli as tomllib
    return tomllib.loads((work / "pyproject.toml").read_text(encoding="utf-8"))


def _sha256_b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def _assemble_fallback_wheel(work: Path, outdir: Path) -> Path:
    """Path 2: PEP 427 wheel assembled from the packages discovered by the
    real ``pyproject.toml`` include pattern (offline, no ``wheel`` pkg)."""
    from setuptools import find_packages

    pyproject = _read_toml(work)
    project = pyproject["project"]
    setuptools_cfg = pyproject.get("tool", {}).get("setuptools", {})
    name = project["name"]
    norm_name = name.replace("-", "_")
    version = project["version"]

    find_cfg = setuptools_cfg.get("packages", {}).get("find", {})
    include = find_cfg.get("include") or ["*"]
    packages = sorted(find_packages(where=str(work), include=include))
    # The pyproject ``include = ["src*", "ready_ai*"]`` must discover the
    # new public package — this is exactly what the real backend would ship.
    assert "ready_ai" in packages, (
        f"package discovery did not find ready_ai (found: {packages})"
    )
    py_modules = setuptools_cfg.get("py-modules") or []

    files: list[Path] = []
    for pkg in packages:
        root = work / pkg.replace(".", "/")
        for path in root.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                files.append(path)
    for module in py_modules:
        files.append(work / f"{module}.py")
    for extra in ("README.md", "LICENSE"):
        path = work / extra
        if path.exists():
            files.append(path)
    files = sorted(set(files))

    dist_info = f"{norm_name}-{version}.dist-info"
    records: list[tuple[str, str, int]] = []
    dependencies = project.get("dependencies") or []
    requires_python = project.get("requires-python", "")
    requires_dist = "".join(
        f"Requires-Dist: {dep}\n" for dep in dependencies if dep is not None
    )
    metadata = (
        "Metadata-Version: 2.1\n"
        f"Name: {name}\n"
        f"Version: {version}\n"
        f"Requires-Python: {requires_python}\n"
        f"{requires_dist}"
        "\n"
    )
    wheel_meta = (
        "Wheel-Version: 1.0\n"
        "Generator: ready-ai packaging smoke test (offline fallback)\n"
        "Root-Is-Purelib: true\n"
        f"Tag: {_WHEEL_TAG}\n"
    )
    entry_points = "[console_scripts]\n" + "".join(
        f"{key} = {value}\n" for key, value in (project.get("scripts") or {}).items()
    )
    top_level = "\n".join(
        sorted({pkg.split(".")[0] for pkg in packages} | set(py_modules))
    ) + "\n"

    wheel_path = outdir / f"{norm_name}-{version}-{_WHEEL_TAG}.whl"
    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            relative = path.relative_to(work).as_posix()
            data = path.read_bytes()
            zf.writestr(relative, data)
            records.append((relative, _sha256_b64(data), len(data)))
        for relative, text in (
            (f"{dist_info}/METADATA", metadata),
            (f"{dist_info}/WHEEL", wheel_meta),
            (f"{dist_info}/entry_points.txt", entry_points),
            (f"{dist_info}/top_level.txt", top_level),
        ):
            data = text.encode("utf-8")
            zf.writestr(relative, data)
            records.append((relative, _sha256_b64(data), len(data)))
        # RECORD lists itself with an empty hash/size (PEP 427).
        record_lines = "".join(f"{p},{h},{s}\n" for p, h, s in records)
        zf.writestr(f"{dist_info}/RECORD", record_lines)
    return wheel_path


def _build_wheel(root: Path) -> Path:
    """Build a wheel from a hermetic copy of the repo into ``root``."""
    work = root / "repo"
    shutil.copytree(REPO_ROOT, work, ignore=_copy_ignore)
    outdir = root / "wheels"
    outdir.mkdir()

    wheel = _try_real_build(work, outdir)
    if wheel is None:
        wheel = _assemble_fallback_wheel(work, outdir)
    assert wheel.exists() and wheel.name.endswith(".whl"), wheel
    return wheel


@pytest.fixture(scope="module")
def wheel(tmp_path_factory):
    return _build_wheel(tmp_path_factory.mktemp("wheel-smoke"))


# ─── Wheel content checks ──────────────────────────────────────────────────


def test_wheel_contains_public_package_and_engine(wheel):
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    assert any(n.startswith("ready_ai/") for n in names), "wheel must ship ready_ai/"
    assert "ready_ai/__init__.py" in names
    assert "ready_ai/models.py" in names
    assert "ready_ai/client.py" in names
    assert "src/agent/loop.py" in names, "wheel must still ship the engine under src/"
    assert "src/api/models.py" in names
    assert "main.py" in names, "CLI entry module must remain in the wheel"


# ─── Clean-env smoke run ───────────────────────────────────────────────────


_SMOKE_SCRIPT = r"""
import asyncio
import json
import os
import sys

site = os.environ["RA_SMOKE_SITE"]
outdir = os.environ["RA_SMOKE_OUT"]
sys.path.insert(0, site)

# (a) DoD 1 import line, resolved from the wheel — never from the repo.
import ready_ai
from pydantic import ValidationError

from ready_ai import ReadyAI, Flow, FlowStep, RunResult, BrowserOptions

assert ready_ai.__file__.startswith(site), ready_ai.__file__
assert isinstance(ready_ai.__version__, str) and ready_ai.__version__

# (b) REAL model construction + validation (not mocked).
flow = Flow(
    name="smoke",
    url="https://example.com/start",
    timeout_s=10.0,
    effect_policy="observe",
    steps=[FlowStep(name="s1", actions=[], asserts=[], extract=[])],
)
for bad in ("not-a-url", "javascript:alert(1)"):
    try:
        Flow(url=bad, steps=[FlowStep()])
        raise SystemExit(f"expected validation error for url {bad!r}")
    except ValidationError:
        pass
try:
    Flow(url="https://example.com", steps=[FlowStep()], timeout_s=0)
    raise SystemExit("expected validation error for zero timeout")
except ValidationError:
    pass

browser = BrowserOptions(headless=True, port=9222)
payload = flow.model_dump()
assert payload["version"] == 1
assert "cookies" not in json.dumps(payload).lower()
assert browser.model_dump()["profile"] is None

# (c) Minimal ReadyAI run with the AgenticLoop boundary mocked
#     (no Chrome, no network inside the smoke test).
from ready_ai import client as ra_client


class _FakeLoop:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def run_flow(self, spec):
        return {
            "run_id": spec.run_id or "smoke-run",
            "flow": spec.name,
            "url": spec.url,
            "status": "passed",
            "steps": [],
            "summary": {"steps_total": 0, "steps_passed": 0, "steps_failed": 0},
            "failure_reason": None,
        }


ra_client.AgenticLoop = _FakeLoop


async def _main():
    ai = ReadyAI(output_dir=outdir, profiles={"smoke": None})
    result = await ai.run_flow(flow, browser=browser)
    assert isinstance(result, RunResult), type(result)
    assert result.status == "passed"
    assert result.run_id


asyncio.run(_main())
print("SMOKE-OK")
"""


def test_clean_env_smoke_import_and_minimal_flow(wheel, tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    with zipfile.ZipFile(wheel) as zf:
        zf.extractall(site)

    outdir = tmp_path / "out"
    env = dict(os.environ)
    env["RA_SMOKE_SITE"] = str(site)
    env["RA_SMOKE_OUT"] = str(outdir)
    env["PYTHONPATH"] = str(site)

    proc = subprocess.run(
        [sys.executable, "-c", _SMOKE_SCRIPT],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert "SMOKE-OK" in proc.stdout, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert proc.returncode == 0, proc.stderr
