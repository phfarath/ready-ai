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
        run_id="test-run-123",
        goal="Criar conta",
        url="https://example.com/signup",
        doc_steps=[
            DocStepState(number=1, title="Abrir página de cadastro", action_description="navigate", annotation="ok"),
            DocStepState(number=2, title="Preencher formulário", action_description="type_text", annotation="ok"),
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
        import pytest

        path = tmp_path / "bad.json"
        path.write_text("not json")
        with pytest.raises(ValueError, match="corrupt"):
            RunState.from_file(path)
        assert "Failed to load checkpoint" in caplog.text
        assert (tmp_path / "bad.json.corrupt").exists()

    def test_run_state_confirmed_effects_roundtrip(self, tmp_path: Path):
        state = RunState(run_id="r", goal="g", url="https://example.com")
        state.confirmed_effects.append("r:step-1")
        path = tmp_path / "state.json"
        state.to_file(path)
        restored = RunState.from_file(path)
        assert restored is not None
        assert restored.confirmed_effects == ["r:step-1"]


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


# ─── executor.py tests (T1 — PLAN_FASE_A Task 5) ─────────────────────────

def _executor_doubles(**overrides):
    """Page/input/runtime/llm doubles wired for execute_step (no Chrome)."""
    page = MagicMock()
    page.event_cursor = 0
    page.wait_for_navigation_settled = AsyncMock(return_value=False)
    page.http_failures_since = MagicMock(return_value=[])
    page.get_dom_html = AsyncMock(return_value="<html>after</html>")
    runtime = MagicMock()
    runtime.get_state_fingerprint = AsyncMock(return_value="fp")
    runtime.evaluate = AsyncMock(return_value="https://app.test")
    runtime.get_interactive_elements = AsyncMock(return_value="[]")
    input_domain = MagicMock()
    llm = MagicMock()
    llm.complete = AsyncMock(return_value='{"action": "observe"}')
    doubles = {
        "page": page,
        "input_domain": input_domain,
        "runtime": runtime,
        "llm": llm,
    }
    doubles.update(overrides)
    return doubles


class TestExecutor:
    async def test_observe_succeeds_first_attempt(self):
        from unittest.mock import patch

        from src.agent.executor import execute_step

        d = _executor_doubles()
        with patch(
            "src.agent.executor._dispatch_action",
            AsyncMock(return_value="Observing current page state"),
        ):
            result = await execute_step(
                "look", "<html></html>", "[]", d["llm"],
                d["page"], d["input_domain"], d["runtime"],
            )
        assert result.success is True
        assert result.attempts == 1

    async def test_click_with_dom_change_succeeds(self):
        from unittest.mock import patch

        from src.agent.executor import execute_step

        d = _executor_doubles()
        d["runtime"].get_state_fingerprint = AsyncMock(
            side_effect=["before", "after"]
        )
        with patch(
            "src.agent.executor._get_action",
            AsyncMock(return_value={"action": "click", "selector": "#go"}),
        ), patch(
            "src.agent.executor._dispatch_action",
            AsyncMock(return_value="Clicked element: #go"),
        ):
            result = await execute_step(
                "click go", "<html></html>", "[]", d["llm"],
                d["page"], d["input_domain"], d["runtime"],
            )
        assert result.success is True
        assert "Clicked element: #go" in result.action_desc

    async def test_unchanged_dom_retries_then_fails(self):
        from unittest.mock import patch

        from src.agent.executor import MAX_RETRIES, execute_step

        d = _executor_doubles()
        d["runtime"].get_state_fingerprint = AsyncMock(return_value="same")
        with patch(
            "src.agent.executor._get_action",
            AsyncMock(return_value={"action": "click", "selector": "#stale"}),
        ), patch(
            "src.agent.executor._dispatch_action",
            AsyncMock(return_value="Clicked element: #stale"),
        ):
            result = await execute_step(
                "click stale", "<html></html>", "[]", d["llm"],
                d["page"], d["input_domain"], d["runtime"],
            )
        assert result.success is False
        assert result.attempts == MAX_RETRIES
        assert "FAILED" in result.action_desc

    async def test_unparseable_action_exhausts_retries(self):
        from unittest.mock import patch

        from src.agent.executor import MAX_RETRIES, execute_step

        d = _executor_doubles()
        with patch(
            "src.agent.executor._get_action", AsyncMock(return_value=None)
        ):
            result = await execute_step(
                "mystery", "<html></html>", "[]", d["llm"],
                d["page"], d["input_domain"], d["runtime"],
            )
        assert result.success is False
        assert result.attempts == MAX_RETRIES
        assert "unparseable" in result.failure_reason

    async def test_previous_failures_feed_retry_prompt(self):
        from unittest.mock import patch

        from src.agent.executor import execute_step

        d = _executor_doubles()
        seen: list = []
        orig_get_action = __import__(
            "src.agent.executor", fromlist=["_get_action"]
        )._get_action

        async def _spy(step, dom, els, llm, failures, url=None):
            seen.append(list(failures))
            return await orig_get_action(step, dom, els, llm, failures, url)

        with patch("src.agent.executor._get_action", _spy), patch(
            "src.agent.executor._dispatch_action",
            AsyncMock(return_value="Observing current page state"),
        ):
            result = await execute_step(
                "look", "<html></html>", "[]", d["llm"],
                d["page"], d["input_domain"], d["runtime"],
                previous_failures=["Attempt 1: boom"],
            )
        assert result.success is True
        assert seen and seen[0] == ["Attempt 1: boom"]


# ─── loop.py tests (T2 — PLAN_FASE_A Task 5) ─────────────────────────────────

def _loop_with_session(tmp_path: Path, monkeypatch):
    """AgenticLoop with stub session domains (no Chrome, no LLM calls)."""
    from src.agent.loop import AgenticLoop

    loop = AgenticLoop(
        goal="T1T2 core",
        url="https://app.test",
        output_dir=str(tmp_path),
        headless=True,
    )
    loop._save_checkpoint = lambda *a, **k: None
    page = MagicMock()
    page.get_dom_html = AsyncMock(return_value="<html>stub</html>")
    page.wait_for_network_idle = AsyncMock(return_value=None)
    page.wait_for_navigation_settled = AsyncMock(return_value=False)
    page.event_cursor = 0
    page.http_failures_since = MagicMock(return_value=[])
    page.screenshot = AsyncMock(return_value="dGVzdA==")
    runtime = MagicMock()
    runtime.get_interactive_elements = AsyncMock(return_value="[]")
    runtime.get_state_fingerprint = AsyncMock(return_value="fp")
    runtime.evaluate = AsyncMock(return_value="https://app.test")
    monkeypatch.setattr(loop._session, "_page", page)
    monkeypatch.setattr(loop._session, "_input", MagicMock())
    monkeypatch.setattr(loop._session, "_runtime", runtime)
    return loop, page, runtime


class TestLoop:
    async def test_execute_steps_runs_each_planned_step(
        self, tmp_path: Path, monkeypatch
    ):
        from unittest.mock import patch

        from src.agent.executor import StepResult
        from src.docs.renderer import DocRenderer

        loop, _, _ = _loop_with_session(tmp_path, monkeypatch)
        calls: list[str] = []

        async def _fake_execute(step, *a, **k):
            calls.append(step)
            return StepResult(
                action_desc=f"Clicked element: #{step}",
                success=True,
                retry_needed=False,
                attempts=1,
            )

        monkeypatch.setattr("src.agent.loop.executor.execute_step", _fake_execute)
        monkeypatch.setattr(
            "src.agent.loop.CursorAnimator.highlight_element", AsyncMock()
        )
        monkeypatch.setattr(
            "src.agent.loop.CursorAnimator.clear_highlight", AsyncMock()
        )
        annotation = MagicMock()
        annotation.complete_with_vision = AsyncMock(return_value="note")
        with patch(
            "src.agent.loop.recovery.dom_fingerprint", AsyncMock(return_value="fp")
        ):
            results = await loop._execute_steps(
                ["one", "two"], MagicMock(), annotation, DocRenderer("G")
            )
        assert calls == ["one", "two"]
        assert all(r.success for r in results)
        assert loop._state.current_step_index == 2

    async def test_execute_steps_records_failure_and_continues(
        self, tmp_path: Path, monkeypatch
    ):
        from unittest.mock import patch

        from src.agent.executor import StepResult
        from src.docs.renderer import DocRenderer

        loop, _, _ = _loop_with_session(tmp_path, monkeypatch)

        async def _fake_execute(step, *a, **k):
            ok = step != "bad"
            return StepResult(
                action_desc=f"Step {step}",
                success=ok,
                retry_needed=False,
                attempts=1,
                failure_reason="" if ok else "boom",
            )

        monkeypatch.setattr("src.agent.loop.executor.execute_step", _fake_execute)
        monkeypatch.setattr(
            "src.agent.loop.CursorAnimator.highlight_element", AsyncMock()
        )
        monkeypatch.setattr(
            "src.agent.loop.CursorAnimator.clear_highlight", AsyncMock()
        )
        annotation = MagicMock()
        annotation.complete_with_vision = AsyncMock(return_value="note")
        with patch(
            "src.agent.loop.recovery.dom_fingerprint", AsyncMock(return_value="fp")
        ):
            results = await loop._execute_steps(
                ["good", "bad"], MagicMock(), annotation, DocRenderer("G")
            )
        assert [r.success for r in results] == [True, False]
        assert "boom" in results[1].failure_reason

    async def test_execute_steps_empty_plan_returns_empty(
        self, tmp_path: Path, monkeypatch
    ):
        from src.docs.renderer import DocRenderer

        loop, _, _ = _loop_with_session(tmp_path, monkeypatch)
        results = await loop._execute_steps(
            [], MagicMock(), MagicMock(), DocRenderer("G")
        )
        assert results == []


# ─── Locator / Action Fixtures (READY-AI-T-5) ────────────────────────────

@pytest.fixture
def locator_fixture_success():
    """Fixture: locator resolves via role+name and action succeeds."""
    return {"method": "role+name", "selector": '[role="button"][name="Submit"]', "visible": True}

@pytest.fixture
def locator_fixture_covered_target():
    """Fixture: target covered by overlay; action fails hit-target check."""
    return {"method": "css", "selector": "#submit", "visible": True, "hit_target_ok": False}

@pytest.fixture
def locator_fixture_fallback():
    """Fixture: role+name fails; falls back to text; then CSS."""
    return {"fallback_path": ["role+name", "text", "css"], "final_selector": "#submit-btn"}

@pytest.fixture
def locator_fixture_not_visible():
    """Fixture: element exists but is not visible; action should fail."""
    return {"method": "data-testid", "selector": '[data-testid="hidden"]', "visible": False, "stable": False}

@pytest.fixture
def locator_fixture_not_reachable():
    """Fixture: element not reachable (stability/hit-target failure)."""
    return {"method": "css", "selector": ".stale-btn", "visible": True, "stable": False, "hit_target_ok": False}


@pytest.fixture
def locator_action_check_fixture():
    """Fixture for check/hover/drag actions with AX tree participation."""
    return {
        "actions": ["check", "hover", "drag", "fill", "select"],
        "ax_role": "checkbox",
        "ax_name": "Accept terms",
        "path_report": "LocatorPath(role=checkbox, name=Accept terms)",
    }
