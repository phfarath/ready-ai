"""
Tests for LLM call instrumentation per phase (READY-AI-T-US2, hypothesis H7).

Pure observability: LLMCallStats + _CountingLLMClient count LLM calls by
pipeline phase (planner/executor/critic/healer/annotation) and surface them
in summary.txt / test_summary.txt without changing any behavior.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent import executor as executor_module
from src.agent.loop import (
    ROLE_TO_PHASE,
    LLMCallStats,
    _CountingLLMClient,
    _instrument_llm,
)
from src.docs.output import render_llm_calls_section, save_docs


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sample_doc"

_PNG_1PX_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


# ─── Unit: LLMCallStats ────────────────────────────────────────────────


class TestLLMCallStats:
    def test_record_increments_per_phase(self):
        stats = LLMCallStats()
        stats.record("planner")
        stats.record("planner")
        stats.record("healer")
        assert stats.as_dict() == {"planner": 2, "healer": 1}
        assert stats.total() == 3

    def test_empty_stats(self):
        stats = LLMCallStats()
        assert stats.as_dict() == {}
        assert stats.total() == 0

    def test_as_dict_returns_copy(self):
        stats = LLMCallStats()
        stats.record("critic")
        snapshot = stats.as_dict()
        snapshot["critic"] = 99
        assert stats.as_dict() == {"critic": 1}

    def test_repr_is_readable(self):
        stats = LLMCallStats()
        stats.record("executor")
        stats.record("executor")
        stats.record("healer")
        text = repr(stats)
        assert "LLMCallStats" in text
        assert "total=3" in text
        assert "executor=2" in text
        assert "healer=1" in text

    def test_repr_empty(self):
        assert repr(LLMCallStats()) == "LLMCallStats(total=0)"


class TestRoleToPhaseMapping:
    def test_canonical_roles_map_to_phases(self):
        assert ROLE_TO_PHASE["planner"] == "planner"
        assert ROLE_TO_PHASE["executor"] == "executor"
        assert ROLE_TO_PHASE["critic"] == "critic"
        assert ROLE_TO_PHASE["recovery"] == "healer"
        assert ROLE_TO_PHASE["annotator"] == "annotation"


# ─── Unit: _CountingLLMClient proxy ────────────────────────────────────


class TestCountingLLMClient:
    @pytest.mark.asyncio
    async def test_counts_complete_by_role(self):
        inner = SimpleNamespace()
        inner.complete = AsyncMock(return_value="ok")
        stats = LLMCallStats()
        proxy = _CountingLLMClient(inner, stats)

        await proxy.complete([{"role": "user", "content": "x"}], role="planner")
        await proxy.complete([{"role": "user", "content": "x"}], role="planner")
        await proxy.complete([{"role": "user", "content": "x"}], role="recovery")

        assert stats.as_dict() == {"planner": 2, "healer": 1}
        assert inner.complete.await_count == 3

    @pytest.mark.asyncio
    async def test_counts_vision_methods_with_default_role(self):
        inner = SimpleNamespace(
            complete_with_vision=AsyncMock(return_value="a"),
            complete_with_vision_multi=AsyncMock(return_value="b"),
        )
        stats = LLMCallStats()
        proxy = _CountingLLMClient(inner, stats)

        await proxy.complete_with_vision(prompt="p", image_b64="img")
        await proxy.complete_with_vision_multi(prompt="p", images_b64=["img"])

        assert stats.as_dict() == {"annotation": 2}

    @pytest.mark.asyncio
    async def test_complete_without_role_falls_back_to_unknown(self):
        inner = SimpleNamespace(complete=AsyncMock(return_value="ok"))
        stats = LLMCallStats()
        proxy = _CountingLLMClient(inner, stats)

        await proxy.complete([{"role": "user", "content": "x"}])

        assert stats.as_dict() == {"unknown": 1}

    @pytest.mark.asyncio
    async def test_unknown_role_becomes_own_bucket(self):
        inner = SimpleNamespace(complete=AsyncMock(return_value="ok"))
        stats = LLMCallStats()
        proxy = _CountingLLMClient(inner, stats)

        await proxy.complete([], role="summarizer")

        assert stats.as_dict() == {"summarizer": 1}

    @pytest.mark.asyncio
    async def test_counts_call_attempt_even_when_delegate_raises(self):
        class Exploding:
            async def complete(self, *args, **kwargs):
                raise RuntimeError("provider down")

        stats = LLMCallStats()
        proxy = _CountingLLMClient(Exploding(), stats)

        with pytest.raises(RuntimeError):
            await proxy.complete([], role="critic")

        assert stats.as_dict() == {"critic": 1}

    def test_delegates_arbitrary_attributes_and_methods(self):
        inner = MagicMock()
        inner.model = "gpt-test"
        inner.some_sync_helper = lambda: 42  # noqa: E731
        stats = LLMCallStats()
        proxy = _CountingLLMClient(inner, stats)

        assert proxy.model == "gpt-test"
        assert proxy.some_sync_helper() == 42

    def test_instrument_llm_none_passthrough(self):
        stats = LLMCallStats()
        assert _instrument_llm(None, stats) is None

    def test_instrument_llm_wraps_client(self):
        inner = MagicMock()
        proxied = _instrument_llm(inner, LLMCallStats())
        assert isinstance(proxied, _CountingLLMClient)


# ─── Executor integration through the real execute_step ───────────────


class TestRealExecutorThroughProxy:
    @pytest.mark.asyncio
    async def test_execute_step_counts_one_executor_call(self):
        stats = LLMCallStats()
        llm = MagicMock()
        llm.complete = AsyncMock(return_value='{"action": "observe"}')

        page = MagicMock()
        page.event_cursor = 0
        page.http_failures_since = None
        page.wait_for_navigation_settled = AsyncMock()

        runtime = MagicMock()
        runtime.evaluate = AsyncMock(return_value="https://app.example.com/page")
        runtime.get_state_fingerprint = AsyncMock(return_value="fp-1")

        result = await executor_module.execute_step(
            "Verify the dashboard loads",
            "<html><body></body></html>",
            "[]",
            _instrument_llm(llm, stats),
            page,
            MagicMock(),
            runtime,
        )

        assert result.success is True
        assert result.attempts == 1
        assert stats.as_dict() == {"executor": 1}
        assert llm.complete.await_count == 1


# ─── save_docs summary.txt section ────────────────────────────────────


class TestSaveDocsLLMCallsSection:
    def test_without_llm_calls_output_is_byte_identical_to_legacy(self, tmp_path):
        out = tmp_path / "out-legacy"
        save_docs("# md", {"step_01.png": _PNG_1PX_B64}, str(out))

        summary = (out / "summary.txt").read_text(encoding="utf-8")
        assert summary == (
            "Generated documentation\n"
            "Steps: 1\n"
            "Screenshots: ['step_01.png']\n"
        )

    def test_with_empty_llm_calls_appends_zero_total(self, tmp_path):
        out = tmp_path / "out-empty"
        save_docs("# md", {}, str(out), llm_calls={})

        summary = (out / "summary.txt").read_text(encoding="utf-8")
        assert "LLM calls by phase:" in summary
        assert "total: 0" in summary

    def test_with_llm_calls_orders_by_count_desc_then_name(self, tmp_path):
        out = tmp_path / "out-counts"
        save_docs(
            "# md",
            {},
            str(out),
            llm_calls={"planner": 1, "healer": 5, "executor": 12},
        )

        summary = (out / "summary.txt").read_text(encoding="utf-8")
        expected_tail = (
            "LLM calls by phase:\n"
            "  executor: 12\n"
            "  healer: 5\n"
            "  planner: 1\n"
            "  total: 18\n"
        )
        assert summary.endswith(expected_tail)

    def test_render_llm_calls_section_formatting(self):
        section = render_llm_calls_section({"annotation": 2, "executor": 2})
        assert section.splitlines() == [
            "LLM calls by phase:",
            "  annotation: 2",
            "  executor: 2",
            "  total: 4",
        ]


# ─── Integration: AgenticLoop.run() doc-mode wiring ───────────────────


def _make_loop(tmp_path):
    """AgenticLoop with a fully mocked BrowserSession (doc-mode)."""
    from src.agent.loop import AgenticLoop

    loop = AgenticLoop(
        goal="instrumented docs run",
        url="https://app.example.com/start",
        output_dir=str(tmp_path),
        headless=True,
    )
    session = loop._session
    session.setup = AsyncMock(return_value=None)
    session.teardown = AsyncMock(return_value=None)
    session.inject_cookies = AsyncMock(return_value=None)
    session.handle_login = AsyncMock(return_value=None)
    session.cookies_file = None
    session.username = None
    session.password = None

    page = MagicMock()
    page.enable = AsyncMock(return_value=None)
    page.navigate = AsyncMock(return_value=None)
    session._page = page

    cursor = MagicMock()
    cursor.stop = AsyncMock(return_value=None)
    cursor.moving = False
    loop._cursor = cursor

    return loop


@pytest.mark.asyncio
async def test_doc_mode_run_wires_stats_into_summary_txt(tmp_path, monkeypatch):
    """run() counts calls made against llm/annotation_llm and lands them in
    summary.txt via save_docs."""
    import src.agent.loop as loop_module

    loop = _make_loop(tmp_path)

    async def fake_resolve(llm, doc):
        return ["Step 1"]

    async def fake_execute(steps, llm, annotation_llm, doc, *args, **kwargs):
        await llm.complete([{"role": "user", "content": "act"}], role="executor")
        await llm.complete([{"role": "user", "content": "act"}], role="executor")
        await annotation_llm.complete_with_vision("annotate", "b64img")
        return []

    async def fake_critic(markdown, llm, annotation_llm, doc, step_results):
        return None

    monkeypatch.setattr(loop, "_resolve_steps", AsyncMock(side_effect=fake_resolve))
    monkeypatch.setattr(loop, "_execute_steps", AsyncMock(side_effect=fake_execute))
    monkeypatch.setattr(loop, "_critic_loop", AsyncMock(side_effect=fake_critic))
    monkeypatch.setattr(loop, "_save_checkpoint", lambda *a, **k: None)
    monkeypatch.setattr(loop, "_save_metrics", lambda *a, **k: None)

    # run() imports LLMClient at call time from src.llm.client — patch there
    # so the proxies wrap fakes instead of real network clients.
    fake_main = MagicMock()
    fake_main.complete = AsyncMock(return_value="ok")
    fake_annotation = MagicMock()
    fake_annotation.complete_with_vision = AsyncMock(return_value="annotated")
    monkeypatch.setattr(
        "src.llm.client.LLMClient",
        MagicMock(side_effect=[fake_main, fake_annotation]),
    )

    captured: dict = {}
    real_save_docs = loop_module.save_docs

    def recording_save_docs(*args, **kwargs):
        captured["llm_calls"] = kwargs.get("llm_calls")
        return real_save_docs(*args, **kwargs)

    monkeypatch.setattr(loop_module, "save_docs", recording_save_docs)

    await loop.run()

    # Proxies were in place during the run and stats accumulated per phase.
    assert isinstance(loop.llm, _CountingLLMClient)
    assert captured["llm_calls"] == {"executor": 2, "annotation": 1}

    summary = (tmp_path / "summary.txt").read_text(encoding="utf-8")
    assert "LLM calls by phase:" in summary
    assert "executor: 2" in summary
    assert "annotation: 1" in summary
    assert "total: 3" in summary


# ─── H7 baseline: stable DocTestRunner flow (all steps pass) ──────────


def _baseline_runner_mocks():
    """CDP/browser mocks mirroring tests/test_e2e_doc_test.py, but keeping
    the REAL executor module so executor LLM calls actually flow through
    the counting proxy."""
    chrome_proc = MagicMock()
    chrome_proc.terminate = MagicMock()
    chrome_proc.kill = MagicMock()
    chrome_proc.poll = MagicMock(return_value=0)
    chrome_proc.returncode = 0
    chrome_proc.pid = 1234

    conn = AsyncMock()
    conn.connect = AsyncMock()
    conn.attach_to_page = AsyncMock()
    conn.close = AsyncMock()

    page = MagicMock()
    page.enable = AsyncMock()
    page.navigate = AsyncMock()
    page.get_dom_html = AsyncMock(
        return_value="<html><body><button id='login-btn'>Login</button></body></html>"
    )
    page.wait_for_network_idle = AsyncMock()
    page.wait_for_navigation_settled = AsyncMock()
    page.wait_for_selector = AsyncMock(return_value=True)
    page.event_cursor = 0
    page.http_failures_since = None

    input_domain = MagicMock()

    runtime = MagicMock()
    runtime.evaluate = AsyncMock(return_value="https://app.example.com/dash")
    runtime.get_state_fingerprint = AsyncMock(return_value="fp-stable")
    runtime.get_interactive_elements = AsyncMock(
        return_value='[{"tag": "button", "id": "login-btn", "text": "Login"}]'
    )

    # Stable plan: every step resolves to a no-op observe action → exactly
    # one role="executor" call per documented step, no retries.
    llm = MagicMock()
    llm.complete = AsyncMock(return_value='{"action": "observe"}')

    return chrome_proc, conn, page, input_domain, runtime, llm


def _file_to_b64(path: Path) -> str:
    import base64

    return base64.b64encode(path.read_bytes()).decode()


class TestH7BaselineStableDocTestFlow:
    """
    DoD2 baseline (stable replay): DocTestRunner re-executes the 2-step
    sample_doc fixture where every step passes. Expected LLM usage of a
    stable doc-test replay:

        planner: 0   (no planning on replay)
        executor: 2  (one role="executor" call per documented step)
        critic:  0
        healer:  0   (auto_heal=False, nothing breaks or drifts)
        total:   2
    """

    @pytest.mark.asyncio
    async def test_stable_flow_llm_calls_land_in_test_summary(self, tmp_path):
        from src.agent.test_runner import DocTestRunner

        doc_dir = tmp_path / "sample_doc"
        shutil.copytree(FIXTURE_DIR, doc_dir)
        output_dir = tmp_path / "test-report"

        chrome_proc, conn, page, input_domain, runtime, llm = _baseline_runner_mocks()
        page.screenshot = AsyncMock(
            side_effect=[
                _file_to_b64(doc_dir / "screenshots" / "step_01.png"),
                _file_to_b64(doc_dir / "screenshots" / "step_02.png"),
            ]
        )
        dom_fp_mock = AsyncMock(return_value="abc123")

        with patch("src.agent.test_runner.launch_chrome", return_value=chrome_proc), \
             patch(
                 "src.agent.test_runner.get_ws_url",
                 new_callable=AsyncMock,
                 return_value="ws://localhost:9222",
             ), \
             patch("src.agent.test_runner.CDPConnection", return_value=conn), \
             patch("src.agent.test_runner.PageDomain", return_value=page), \
             patch("src.agent.test_runner.InputDomain", return_value=input_domain), \
             patch("src.agent.test_runner.RuntimeDomain", return_value=runtime), \
             patch("src.agent.test_runner.LLMClient", return_value=llm), \
             patch("src.agent.test_runner._dom_fingerprint", dom_fp_mock):

            runner = DocTestRunner(
                doc_path=str(doc_dir / "docs.md"),
                url="https://app.example.com/dash",
                output_dir=str(output_dir),
                threshold=0.85,
            )
            report = await runner.run()

        assert report.overall_status == "PASSED"
        assert all(r.status == "PASSED" for r in report.results)

        # Baseline numbers (H7): a stable replay still spends 1 executor
        # LLM call per step — zero-token replay does NOT hold today.
        assert report.llm_calls == {"executor": 2}

        summary_text = (output_dir / "test_summary.txt").read_text(encoding="utf-8")
        assert "Test Report: PASSED" in summary_text
        expected_section = (
            "LLM calls by phase:\n"
            "  executor: 2\n"
            "  total: 2\n"
        )
        assert summary_text.endswith(expected_section)

        report_data = json.loads((output_dir / "test_report.json").read_text())
        assert report_data["llm_calls"] == {"executor": 2}

        # The underlying client was reached exactly once per step.
        assert llm.complete.await_count == 2
