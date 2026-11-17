"""Internal compatibility (READY-AI-T-13, DoD 4).

The public SDK must be purely additive: internal ``ready_ai._engine.*`` imports and the
existing CLI keep working during the transition.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_legacy_top_level_modules_are_not_packages():
    """0.9.0 no longer installs top-level ``src`` or ``main``."""
    import importlib

    for name in ("src", "main"):
        try:
            importlib.import_module(name)
        except ModuleNotFoundError:
            continue
        raise AssertionError(f"{name} is still importable as a top-level module")


def test_src_module_imports_still_work():
    """DoD 4 — internal module graph is untouched by the SDK."""
    from ready_ai._engine.agent.loop import AgenticLoop  # noqa: F401
    from ready_ai._engine.agent.state import RunState  # noqa: F401
    from ready_ai._engine.api import server  # noqa: F401
    from ready_ai._engine.api.batch_loader import load_flow_config  # noqa: F401
    from ready_ai._engine.api.manager import RunManager  # noqa: F401
    from ready_ai._engine.api.models import FlowRunResult, FlowSpec  # noqa: F401

    assert FlowSpec and FlowRunResult and RunState and AgenticLoop and RunManager


def test_src_flow_models_still_validate():
    from ready_ai._engine.api.models import FlowSpec, FlowStepSpec, FlowAction

    spec = FlowSpec(
        url="https://app.example.com",
        steps=[FlowStepSpec(actions=[FlowAction(action="observe")])],
    )
    assert spec.steps[0].actions[0].action == "observe"


def test_cli_help_still_works():
    """DoD 4 — the existing CLI survives the SDK (subcommand help)."""
    env = dict(os.environ)
    # The CLI banner renders an emoji; force UTF-8 on the child so the
    # captured help decodes on platforms whose default encoding is cp1252.
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-m", "ready_ai", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    for subcommand in ("run", "test", "api", "batch", "run-flow", "export"):
        assert subcommand in proc.stdout, f"CLI must still expose {subcommand!r}"


def test_default_browser_session_construction_still_works(tmp_path):
    """DoD 4 — AgenticLoop() construction (the CLI/API wiring) is unchanged."""
    from unittest.mock import patch

    from ready_ai._engine.agent.loop import AgenticLoop

    with patch("ready_ai._engine.agent.loop.BrowserSession") as mock_session:
        loop = AgenticLoop(
            goal="g",
            url="https://app.example.com",
            output_dir=str(tmp_path),
            run_id="compat",
        )
        mock_session.assert_called_once()
    assert loop.run_id == "compat"
