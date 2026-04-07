import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main import RunConfigError, parse_args, resolve_run_args
from src.agent.loop import AgenticLoop
from src.agent.state import RunState


class _PlanPage:
    async def enable(self):
        return None

    async def navigate(self, url, wait_for_network=True):
        return None

    async def get_dom_html(self, max_length=4000):
        return "<html><body>plan</body></html>"


class _PlanRuntime:
    async def get_interactive_elements(self):
        return "[]"


def test_resolve_run_args_reads_yaml_and_cli_overrides(tmp_path):
    config_path = tmp_path / "ready-ai.yaml"
    config_path.write_text(
        "\n".join(
            [
                "goal: Document account settings",
                "url: https://app.example.com/settings",
                "model: gpt-4o-mini",
                "run_id: from-config",
                "plan_only: true",
            ]
        ),
        encoding="utf-8",
    )

    raw = parse_args([
        "run",
        "--config",
        str(config_path),
        "--model",
        "claude-sonnet-4-20250514",
        "--run-id",
        "from-cli",
    ])
    args = resolve_run_args(raw)

    assert args.goal == "Document account settings"
    assert args.url == "https://app.example.com/settings"
    assert args.model == "claude-sonnet-4-20250514"
    assert args.run_id == "from-cli"
    assert args.plan_only is True


def test_resolve_run_args_reads_toml(tmp_path):
    config_path = tmp_path / "ready-ai.toml"
    config_path.write_text(
        "\n".join(
            [
                'goal = "Document billing"',
                'url = "https://app.example.com/billing"',
                'output = "./tmp-output"',
            ]
        ),
        encoding="utf-8",
    )

    raw = parse_args(["run", "--config", str(config_path)])
    args = resolve_run_args(raw)

    assert args.goal == "Document billing"
    assert args.url == "https://app.example.com/billing"
    assert args.output == "./tmp-output"


def test_resolve_run_args_rejects_unknown_keys(tmp_path):
    config_path = tmp_path / "ready-ai.yaml"
    config_path.write_text("goal: Test\nurl: https://app.example.com\nunexpected: true\n", encoding="utf-8")

    raw = parse_args(["run", "--config", str(config_path)])

    with pytest.raises(RunConfigError, match="Unknown config keys"):
        resolve_run_args(raw)


def test_resolve_run_args_requires_checkpoint_when_resume_requested(tmp_path):
    raw = parse_args([
        "run",
        "--goal",
        "Test",
        "--url",
        "https://app.example.com",
        "--output",
        str(tmp_path),
        "--run-id",
        "resume-run",
        "--resume",
    ])

    with pytest.raises(RunConfigError, match="Checkpoint not found"):
        resolve_run_args(raw)


@pytest.mark.asyncio
async def test_plan_only_creates_checkpoint_without_docs(tmp_path, monkeypatch):
    loop = AgenticLoop(
        goal="Plan login flow",
        url="https://app.example.com",
        output_dir=str(tmp_path),
        run_id="plan-run",
        plan_only=True,
        headless=True,
    )

    monkeypatch.setattr(loop._session, "setup", AsyncMock(return_value=None))
    monkeypatch.setattr(loop._session, "teardown", AsyncMock(return_value=None))
    monkeypatch.setattr(loop._session, "_page", _PlanPage())
    monkeypatch.setattr(loop._session, "_input", SimpleNamespace())
    monkeypatch.setattr(loop._session, "_runtime", _PlanRuntime())

    plan_mock = AsyncMock(return_value=["Click Sign In", "Verify dashboard loads"])
    monkeypatch.setattr("src.agent.loop.planner.plan", plan_mock)

    result_path = await loop.run()

    assert result_path == str(Path(tmp_path) / "plan-run_state.json")
    assert plan_mock.await_count == 1
    assert loop._state.status == "PLANNED"
    assert loop._state.planned_steps == ["Click Sign In", "Verify dashboard loads"]
    assert not (Path(tmp_path) / "docs.md").exists()
    assert json.loads((Path(tmp_path) / "plan-run_state.json").read_text())["status"] == "PLANNED"


@pytest.mark.asyncio
async def test_plan_only_resume_reuses_saved_plan(tmp_path, monkeypatch):
    state_path = Path(tmp_path) / "resume-plan_state.json"
    RunState(
        run_id="resume-plan",
        goal="Plan login flow",
        url="https://app.example.com",
        status="PLANNED",
        planned_steps=["Open the login page", "Verify the dashboard loads"],
        current_step_index=1,
    ).to_file(state_path)

    loop = AgenticLoop(
        goal="Plan login flow",
        url="https://app.example.com",
        output_dir=str(tmp_path),
        run_id="resume-plan",
        resume_from=str(state_path),
        plan_only=True,
        headless=True,
    )

    monkeypatch.setattr(loop._session, "setup", AsyncMock(return_value=None))
    monkeypatch.setattr(loop._session, "teardown", AsyncMock(return_value=None))
    monkeypatch.setattr(loop._session, "_page", _PlanPage())
    monkeypatch.setattr(loop._session, "_input", SimpleNamespace())
    monkeypatch.setattr(loop._session, "_runtime", _PlanRuntime())

    plan_mock = AsyncMock(side_effect=AssertionError("planner.plan should not be called"))
    monkeypatch.setattr("src.agent.loop.planner.plan", plan_mock)

    result_path = await loop.run()

    assert result_path == str(state_path)
    assert loop._state.planned_steps == ["Open the login page", "Verify the dashboard loads"]
