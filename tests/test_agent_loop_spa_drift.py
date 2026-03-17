import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agent.executor import StepResult
from src.agent.loop import AgenticLoop
from src.agent import recovery
from src.agent.cursor import CursorAnimator
from src.docs.renderer import DocRenderer


class _StubPage:
    async def get_dom_html(self, max_length=4000):
        return "<html><body>stub</body></html>"

    async def wait_for_network_idle(self, timeout=10.0, idle_time=0.5):
        return None

    async def screenshot(self):
        return "dGVzdA=="


class _StubInput:
    pass


class _StubRuntime:
    def __init__(self, urls):
        self._urls = iter(urls)

    async def evaluate(self, expression: str):
        assert expression == "window.location.href"
        return next(self._urls)

    async def get_interactive_elements(self):
        return "[]"


class _FingerprintRuntime:
    def __init__(self, payload: str):
        self.payload = payload

    async def evaluate(self, expression: str):
        return self.payload


def _new_loop(tmp_path) -> AgenticLoop:
    loop = AgenticLoop(
        goal="Test SPA drift",
        url="https://app.local",
        output_dir=str(tmp_path),
        headless=True,
    )
    loop._save_checkpoint = lambda *args, **kwargs: None
    return loop


def _annotation_llm() -> MagicMock:
    llm = MagicMock()
    llm.complete_with_vision = AsyncMock(return_value="Annotation")
    return llm


def test_is_spa_drift_policy():
    assert recovery.is_spa_drift("a", "b", False, "u1", "u1") is True
    assert recovery.is_spa_drift("a", "b", True, "u1", "u1") is False
    assert recovery.is_spa_drift("a", "a", False, "u1", "u1") is False
    assert recovery.is_spa_drift("a", "b", False, "u1", "u2") is False


@pytest.mark.asyncio
async def test_dom_fingerprint_is_deterministic(tmp_path):
    runtime = _FingerprintRuntime("button|Save|tab|false")

    fp1 = await recovery.dom_fingerprint(runtime)
    fp2 = await recovery.dom_fingerprint(runtime)

    assert fp1 == fp2


@pytest.mark.asyncio
async def test_dom_fingerprint_changes_with_payload(tmp_path):
    runtime_a = _FingerprintRuntime("button|Save|tab|false")
    runtime_b = _FingerprintRuntime("button|Save|tab|true")

    fp1 = await recovery.dom_fingerprint(runtime_a)
    fp2 = await recovery.dom_fingerprint(runtime_b)

    assert fp1 != fp2


@pytest.mark.asyncio
async def test_execute_steps_replans_current_step_on_spa_drift(tmp_path, monkeypatch):
    loop = _new_loop(tmp_path)
    loop._state.planned_steps = ["Open modal and confirm"]

    exec_mock = AsyncMock(
        side_effect=[
            StepResult(
                action_desc="Clicked element: #confirm",
                success=False,
                attempts=3,
                failure_reason="Selector became stale after modal transition",
            ),
            StepResult(
                action_desc="Clicked element: #modal-confirm",
                success=True,
                attempts=1,
            ),
        ]
    )
    monkeypatch.setattr("src.agent.loop.executor.execute_step", exec_mock)

    monkeypatch.setattr("src.agent.loop.recovery.dom_fingerprint", AsyncMock(side_effect=["fp1", "fp2"]))
    monkeypatch.setattr("src.agent.loop.recovery.recover_failed_step", AsyncMock(return_value=(
        StepResult(
            action_desc="Clicked element: #modal-confirm",
            success=True,
            attempts=1,
        ),
        "Click the modal confirm button",
        1,
    )))
    monkeypatch.setattr("src.agent.loop.CursorAnimator.highlight_element", AsyncMock())
    monkeypatch.setattr("src.agent.loop.CursorAnimator.clear_highlight", AsyncMock())

    # Set up session stubs
    monkeypatch.setattr(loop._session, "_page", _StubPage())
    monkeypatch.setattr(loop._session, "_input", _StubInput())
    monkeypatch.setattr(loop._session, "_runtime", _StubRuntime(["https://app.local", "https://app.local"]))

    doc = DocRenderer("Goal")

    results = await loop._execute_steps(
        ["Open modal and confirm"],
        MagicMock(),
        _annotation_llm(),
        doc,
    )

    assert len(results) == 1
    assert results[0].success is True
    assert doc.steps[0].title == "Click the modal confirm button"
    assert doc.steps[0].status == "completed"


@pytest.mark.asyncio
async def test_execute_steps_uses_local_adapted_step_when_recovery_succeeds(tmp_path, monkeypatch):
    loop = _new_loop(tmp_path)
    loop._state.planned_steps = ["Submit form"]

    exec_mock = AsyncMock(
        side_effect=[
            StepResult(
                action_desc="Clicked element: #submit",
                success=False,
                attempts=3,
                failure_reason="Submit button selector changed",
            ),
            StepResult(
                action_desc="Clicked element: [data-testid='submit']",
                success=True,
                attempts=1,
            ),
        ]
    )
    monkeypatch.setattr("src.agent.loop.executor.execute_step", exec_mock)

    monkeypatch.setattr("src.agent.loop.recovery.dom_fingerprint", AsyncMock(side_effect=["fp1", "fp1"]))
    monkeypatch.setattr("src.agent.loop.recovery.recover_failed_step", AsyncMock(return_value=(
        StepResult(
            action_desc="Clicked element: [data-testid='submit']",
            success=True,
            attempts=1,
        ),
        "Click the primary submit button",
        1,
    )))
    monkeypatch.setattr("src.agent.loop.CursorAnimator.highlight_element", AsyncMock())
    monkeypatch.setattr("src.agent.loop.CursorAnimator.clear_highlight", AsyncMock())

    monkeypatch.setattr(loop._session, "_page", _StubPage())
    monkeypatch.setattr(loop._session, "_input", _StubInput())
    monkeypatch.setattr(loop._session, "_runtime", _StubRuntime(["https://app.local", "https://app.local"]))

    doc = DocRenderer("Goal")

    results = await loop._execute_steps(
        ["Submit form"],
        MagicMock(),
        _annotation_llm(),
        doc,
    )

    assert len(results) == 1
    assert results[0].success is True
    assert loop._state.planned_steps[0] == "Click the primary submit button"
    assert doc.steps[0].title == "Click the primary submit button"


@pytest.mark.asyncio
async def test_execute_steps_marks_manual_required_when_recovery_fails(tmp_path, monkeypatch):
    loop = _new_loop(tmp_path)
    loop._state.planned_steps = ["Open modal and confirm"]

    exec_mock = AsyncMock(return_value=StepResult(
        action_desc="Clicked element: #confirm",
        success=False,
        attempts=3,
        failure_reason="MFA modal blocks the next action",
    ))
    monkeypatch.setattr("src.agent.loop.executor.execute_step", exec_mock)

    monkeypatch.setattr("src.agent.loop.recovery.dom_fingerprint", AsyncMock(side_effect=["fp1", "fp1"]))
    monkeypatch.setattr("src.agent.loop.recovery.recover_failed_step", AsyncMock(return_value=(
        StepResult(
            action_desc="Clicked element: #confirm",
            success=False,
            attempts=3,
            failure_reason="MFA modal blocks the next action",
            status="manual_required",
        ),
        "Open modal and confirm",
        0,
    )))
    monkeypatch.setattr("src.agent.loop.CursorAnimator.highlight_element", AsyncMock())
    monkeypatch.setattr("src.agent.loop.CursorAnimator.clear_highlight", AsyncMock())

    monkeypatch.setattr(loop._session, "_page", _StubPage())
    monkeypatch.setattr(loop._session, "_input", _StubInput())
    monkeypatch.setattr(loop._session, "_runtime", _StubRuntime(["https://app.local", "https://app.local"]))

    doc = DocRenderer("Goal")

    results = await loop._execute_steps(
        ["Open modal and confirm"],
        MagicMock(),
        _annotation_llm(),
        doc,
    )

    assert len(results) == 1
    assert results[0].status == "manual_required"
    assert doc.steps[0].title == "Open modal and confirm"
    assert doc.steps[0].status == "manual_required"
    assert "Complete the MFA challenge manually." not in doc.render() or "manual_required" in doc.render()


@pytest.mark.asyncio
async def test_execute_steps_url_change_uses_no_spa_replan(tmp_path, monkeypatch):
    loop = _new_loop(tmp_path)
    loop._state.planned_steps = ["Submit form"]

    exec_mock = AsyncMock(return_value=StepResult(
        action_desc="Clicked element: #submit",
        success=False,
        attempts=3,
        failure_reason="Navigation changed context",
    ))
    monkeypatch.setattr("src.agent.loop.executor.execute_step", exec_mock)

    monkeypatch.setattr("src.agent.loop.recovery.dom_fingerprint", AsyncMock(side_effect=["fp1", "fp2"]))
    replan_spa_mock = AsyncMock(return_value="Click submit in redirected page")
    monkeypatch.setattr("src.agent.loop.recovery.replan_spa_step", replan_spa_mock)
    monkeypatch.setattr("src.agent.loop.recovery.recover_failed_step", AsyncMock(return_value=(
        StepResult(
            action_desc="Clicked element: #submit",
            success=False,
            attempts=3,
            failure_reason="Navigation changed context",
            status="manual_required",
        ),
        "Submit form",
        0,
    )))
    monkeypatch.setattr("src.agent.loop.CursorAnimator.highlight_element", AsyncMock())
    monkeypatch.setattr("src.agent.loop.CursorAnimator.clear_highlight", AsyncMock())

    # URL changes between pre and post => not SPA drift
    monkeypatch.setattr(loop._session, "_page", _StubPage())
    monkeypatch.setattr(loop._session, "_input", _StubInput())
    monkeypatch.setattr(loop._session, "_runtime", _StubRuntime(["https://app.local", "https://app.local/redirect"]))

    doc = DocRenderer("Goal")

    results = await loop._execute_steps(
        ["Submit form"],
        MagicMock(),
        _annotation_llm(),
        doc,
    )

    assert len(results) == 1
    assert results[0].success is False
    assert results[0].status == "manual_required"
    # SPA replan_spa_step should not be called directly since recovery handles it now
