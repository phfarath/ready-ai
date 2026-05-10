"""Testes de unidade para o core agent: planner, state, executor, loop.

Estes testes usam mocks para isolar cada componente — não precisam de
Chrome real nem de API da OpenAI.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.planner import plan as planner_plan
from src.agent.state import DocStepState, RunState


# ─── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def fake_llm_client():
    """Devolve um LLMClient mock cujo .complete() pode ser configurado per-test."""
    client = MagicMock()
    client.complete = AsyncMock(return_value="1. Open login page\n2. Enter username")
    client.api_key = "fake-key"
    return client


@pytest.fixture
def fake_browser_session():
    """BrowserSession mock com métodos async stubbed."""
    session = MagicMock()
    session.setup = AsyncMock()
    session.teardown = AsyncMock()
    session.inject_cookies = AsyncMock()
    session.handle_login = AsyncMock(return_value="logged_in")
    session.navigate = AsyncMock()
    session.page = MagicMock()
    session.input_domain = MagicMock()
    session.runtime = MagicMock()
    session.connection = MagicMock()
    return session


@pytest.fixture
def sample_run_state() -> RunState:
    return RunState(
        goal="Criar conta",
        steps=[
            DocStepState(step="1. Abrir página de cadastro", action="navigate", result="ok"),
            DocStepState(step="2. Preencher formulário", action="type_text", result="ok"),
        ],
    )


# ─── state.py tests ──────────────────────────────────────────────────────

class TestStatePersistence:
    def test_run_state_roundtrip(self, tmp_path: Path):
        state = RunState(
            run_id="test-run-1",
            goal="Test Goal",
            url="https://example.com",
            doc_steps=[DocStepState(number=1, title="Open page", action_description="navigate to /", annotation="Start here")],
        )
        path = tmp_path / "state.json"
        state.to_file(path)
        restored = RunState.from_file(path)
        assert restored is not None
        assert restored.goal == "Test Goal"
        assert len(restored.doc_steps) == 1
        assert restored.doc_steps[0].title == "Open page"

    def test_run_state_missing_file_returns_none(self, tmp_path: Path):
        path = tmp_path / "nonexistent.json"
        assert RunState.from_file(path) is None

    def test_run_state_corrupted_json_logs_error(self, tmp_path: Path, caplog):
        path = tmp_path / "bad.json"
        path.write_text("not json")
        result = RunState.from_file(path)
        assert result is None
        assert "Failed to load checkpoint" in caplog.text


# ─── planner.py tests ──────────────────────────────────────────────────────

class TestPlanner:
    async def test_plan_returns_list_of_steps(self, fake_llm_client):
        steps = await planner_plan(
            goal="Documentar login",
            dom_html="<html><body><input id='user'></body></html>",
            interactive_elements=["#user"],
            llm=fake_llm_client,
        )
        assert isinstance(steps, list)
        assert len(steps) == 2
        assert "login" in steps[0].lower()

    async def test_plan_empty_llm_response_returns_empty_list(self, fake_llm_client):
        fake_llm_client.complete.return_value = ""
        steps = await planner_plan(
            goal="Documentar login",
            dom_html="<html></html>",
            interactive_elements=[],
            llm=fake_llm_client,
        )
        assert steps == []

    async def test_plan_parses_different_numbering_styles(self, fake_llm_client):
        fake_llm_client.complete.return_value = (
            "1) First step\n2) Second step\n3) Third step"
        )
        steps = await planner_plan(
            goal="Test numbering",
            dom_html="<html></html>",
            interactive_elements=[],
            llm=fake_llm_client,
        )
        assert len(steps) == 3
        assert steps[0] == "First step"


# ─── placeholder for executor / loop tests (next commit) ───────────────────

class TestExecutorPlaceholder:
    async def test_todo(self):
        """Executor tests will be added in the next iteration."""
        assert True


class TestLoopPlaceholder:
    async def test_todo(self):
        """Loop tests will be added in the next iteration."""
        assert True