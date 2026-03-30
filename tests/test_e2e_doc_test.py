"""
End-to-end integration tests for the DocTestRunner.

Tests the full flow: parse docs → execute steps → compare screenshots → generate report.
Uses mocked CDP/browser so no real Chrome instance is needed.
"""

import asyncio
import base64
import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from src.agent.test_runner import DocTestRunner, DocTestReport


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sample_doc"


def _make_screenshot_b64(color: tuple[int, int, int] = (30, 60, 180)) -> str:
    """Generate a solid-color PNG screenshot as base64."""
    import io
    img = Image.new("RGB", (200, 150), color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _file_to_b64(path: Path) -> str:
    """Read a file and return its base64 encoding."""
    return base64.b64encode(path.read_bytes()).decode()


def _setup_mocks():
    """Create all the CDP mocks needed by DocTestRunner."""
    chrome_proc = MagicMock()
    chrome_proc.terminate = MagicMock()
    chrome_proc.kill = MagicMock()
    chrome_proc.poll = MagicMock(return_value=0)
    chrome_proc.returncode = 0

    conn = AsyncMock()
    conn.connect = AsyncMock()
    conn.attach_to_page = AsyncMock()
    conn.close = AsyncMock()
    conn.send = AsyncMock()

    page = AsyncMock()
    page.enable = AsyncMock()
    page.navigate = AsyncMock()
    page.get_dom_html = AsyncMock(return_value="<html><body><button id='login-btn'>Login</button></body></html>")
    page.wait_for_network_idle = AsyncMock()
    page.wait_for_selector = AsyncMock(return_value=True)

    input_domain = AsyncMock()
    runtime = AsyncMock()
    runtime.get_interactive_elements = AsyncMock(return_value='[{"tag": "button", "id": "login-btn", "text": "Login"}]')

    llm = AsyncMock()

    return chrome_proc, conn, page, input_domain, runtime, llm


@pytest.fixture
def output_dir(tmp_path):
    """Temporary output directory for test reports."""
    out = tmp_path / "test-report"
    out.mkdir()
    return out


@pytest.fixture
def doc_dir(tmp_path):
    """Copy fixture docs to a temp dir so tests don't pollute fixtures."""
    dest = tmp_path / "sample_doc"
    shutil.copytree(FIXTURE_DIR, dest)
    return dest


class TestE2EAllPassed:
    """Scenario: all steps pass (screenshots match baseline)."""

    @pytest.mark.asyncio
    async def test_all_steps_pass(self, doc_dir, output_dir):
        chrome_proc, conn, page, input_domain, runtime, llm = _setup_mocks()

        # Return the exact baseline file bytes so pixel comparison is 1.0
        step_screenshots = [
            _file_to_b64(doc_dir / "screenshots" / "step_01.png"),
            _file_to_b64(doc_dir / "screenshots" / "step_02.png"),
        ]
        page.screenshot = AsyncMock(side_effect=step_screenshots)

        # DOM fingerprint stays the same
        fingerprint = "abc123"
        dom_fp_mock = AsyncMock(return_value=fingerprint)

        # Executor succeeds
        step_result = MagicMock()
        step_result.success = True

        with patch("src.agent.test_runner.launch_chrome", return_value=chrome_proc), \
             patch("src.agent.test_runner.get_ws_url", new_callable=AsyncMock, return_value="ws://localhost:9222"), \
             patch("src.agent.test_runner.CDPConnection", return_value=conn), \
             patch("src.agent.test_runner.PageDomain", return_value=page), \
             patch("src.agent.test_runner.InputDomain", return_value=input_domain), \
             patch("src.agent.test_runner.RuntimeDomain", return_value=runtime), \
             patch("src.agent.test_runner.LLMClient", return_value=llm), \
             patch("src.agent.test_runner._dom_fingerprint", dom_fp_mock), \
             patch("src.agent.test_runner.executor") as mock_executor:

            mock_executor.execute_step = AsyncMock(return_value=step_result)

            runner = DocTestRunner(
                doc_path=str(doc_dir / "docs.md"),
                url="http://localhost:8080",
                output_dir=str(output_dir),
                threshold=0.85,
            )
            report = await runner.run()

        assert report.overall_status == "PASSED"
        assert len(report.results) == 2
        assert all(r.status == "PASSED" for r in report.results)
        assert report.steps_outdated == []
        assert report.steps_broken == []

        # Verify report files were saved
        assert (output_dir / "test_report.json").exists()
        assert (output_dir / "test_summary.txt").exists()

        # Verify JSON structure
        data = json.loads((output_dir / "test_report.json").read_text())
        assert data["overall_status"] == "PASSED"
        assert len(data["results"]) == 2


class TestE2EDriftDetected:
    """Scenario: visual drift detected (screenshots differ from baseline)."""

    @pytest.mark.asyncio
    async def test_drift_detected(self, doc_dir, output_dir):
        chrome_proc, conn, page, input_domain, runtime, llm = _setup_mocks()

        # Screenshot is completely different color (red instead of blue)
        page.screenshot = AsyncMock(return_value=_make_screenshot_b64((255, 0, 0)))

        fingerprint = "abc123"
        dom_fp_mock = AsyncMock(return_value=fingerprint)

        step_result = MagicMock()
        step_result.success = True

        with patch("src.agent.test_runner.launch_chrome", return_value=chrome_proc), \
             patch("src.agent.test_runner.get_ws_url", new_callable=AsyncMock, return_value="ws://localhost:9222"), \
             patch("src.agent.test_runner.CDPConnection", return_value=conn), \
             patch("src.agent.test_runner.PageDomain", return_value=page), \
             patch("src.agent.test_runner.InputDomain", return_value=input_domain), \
             patch("src.agent.test_runner.RuntimeDomain", return_value=runtime), \
             patch("src.agent.test_runner.LLMClient", return_value=llm), \
             patch("src.agent.test_runner._dom_fingerprint", dom_fp_mock), \
             patch("src.agent.test_runner.executor") as mock_executor:

            mock_executor.execute_step = AsyncMock(return_value=step_result)

            runner = DocTestRunner(
                doc_path=str(doc_dir / "docs.md"),
                url="http://localhost:8080",
                output_dir=str(output_dir),
                threshold=0.85,
            )
            report = await runner.run()

        assert report.overall_status == "DRIFT_DETECTED"
        assert len(report.steps_outdated) > 0
        # At least step 1 should have drift (blue baseline vs red current)
        assert 1 in report.steps_outdated

        # Verify diff images were generated
        diffs_dir = output_dir / "diffs"
        assert diffs_dir.exists()

        # Verify report JSON reflects drift
        data = json.loads((output_dir / "test_report.json").read_text())
        assert data["overall_status"] == "DRIFT_DETECTED"


class TestE2EBrokenStep:
    """Scenario: a step execution fails (BROKEN)."""

    @pytest.mark.asyncio
    async def test_broken_step(self, doc_dir, output_dir):
        chrome_proc, conn, page, input_domain, runtime, llm = _setup_mocks()

        # Use exact baseline files so non-broken steps show as PASSED
        step_screenshots = [
            _file_to_b64(doc_dir / "screenshots" / "step_01.png"),
            _file_to_b64(doc_dir / "screenshots" / "step_02.png"),
        ]
        page.screenshot = AsyncMock(side_effect=step_screenshots)

        fingerprint = "abc123"
        dom_fp_mock = AsyncMock(return_value=fingerprint)

        # First step fails, second succeeds
        failed_result = MagicMock()
        failed_result.success = False

        success_result = MagicMock()
        success_result.success = True

        with patch("src.agent.test_runner.launch_chrome", return_value=chrome_proc), \
             patch("src.agent.test_runner.get_ws_url", new_callable=AsyncMock, return_value="ws://localhost:9222"), \
             patch("src.agent.test_runner.CDPConnection", return_value=conn), \
             patch("src.agent.test_runner.PageDomain", return_value=page), \
             patch("src.agent.test_runner.InputDomain", return_value=input_domain), \
             patch("src.agent.test_runner.RuntimeDomain", return_value=runtime), \
             patch("src.agent.test_runner.LLMClient", return_value=llm), \
             patch("src.agent.test_runner._dom_fingerprint", dom_fp_mock), \
             patch("src.agent.test_runner.executor") as mock_executor:

            mock_executor.execute_step = AsyncMock(
                side_effect=[failed_result, success_result]
            )

            runner = DocTestRunner(
                doc_path=str(doc_dir / "docs.md"),
                url="http://localhost:8080",
                output_dir=str(output_dir),
                threshold=0.85,
            )
            report = await runner.run()

        assert report.overall_status == "BROKEN"
        assert 1 in report.steps_broken
        assert report.results[0].status == "BROKEN"
        assert report.results[1].status == "PASSED"

        data = json.loads((output_dir / "test_report.json").read_text())
        assert data["overall_status"] == "BROKEN"


class TestE2EReportStructure:
    """Verify the test report JSON has all expected fields."""

    @pytest.mark.asyncio
    async def test_report_json_structure(self, doc_dir, output_dir):
        chrome_proc, conn, page, input_domain, runtime, llm = _setup_mocks()
        page.screenshot = AsyncMock(return_value=_make_screenshot_b64((30, 60, 180)))
        dom_fp_mock = AsyncMock(return_value="abc123")
        step_result = MagicMock()
        step_result.success = True

        with patch("src.agent.test_runner.launch_chrome", return_value=chrome_proc), \
             patch("src.agent.test_runner.get_ws_url", new_callable=AsyncMock, return_value="ws://localhost:9222"), \
             patch("src.agent.test_runner.CDPConnection", return_value=conn), \
             patch("src.agent.test_runner.PageDomain", return_value=page), \
             patch("src.agent.test_runner.InputDomain", return_value=input_domain), \
             patch("src.agent.test_runner.RuntimeDomain", return_value=runtime), \
             patch("src.agent.test_runner.LLMClient", return_value=llm), \
             patch("src.agent.test_runner._dom_fingerprint", dom_fp_mock), \
             patch("src.agent.test_runner.executor") as mock_executor:

            mock_executor.execute_step = AsyncMock(return_value=step_result)

            runner = DocTestRunner(
                doc_path=str(doc_dir / "docs.md"),
                url="http://localhost:8080",
                output_dir=str(output_dir),
                threshold=0.90,
            )
            report = await runner.run()

        data = json.loads((output_dir / "test_report.json").read_text())

        # Top-level fields
        assert "doc_path" in data
        assert "url" in data
        assert "timestamp" in data
        assert "threshold" in data
        assert "overall_status" in data
        assert "results" in data
        assert "steps_outdated" in data
        assert "steps_broken" in data

        # Per-step fields
        for result in data["results"]:
            assert "step_number" in result
            assert "title" in result
            assert "status" in result
            assert "visual_similarity" in result
            assert "dom_changed" in result

        # Summary text
        summary_text = (output_dir / "test_summary.txt").read_text()
        assert "Test Report:" in summary_text
        assert "Results:" in summary_text


class TestE2EMissingBaseline:
    """Scenario: baseline screenshot is missing — should flag as drift."""

    @pytest.mark.asyncio
    async def test_missing_baseline_flags_drift(self, doc_dir, output_dir):
        # Delete baseline screenshots
        for png in (doc_dir / "screenshots").glob("*.png"):
            png.unlink()

        chrome_proc, conn, page, input_domain, runtime, llm = _setup_mocks()
        page.screenshot = AsyncMock(return_value=_make_screenshot_b64((30, 60, 180)))
        dom_fp_mock = AsyncMock(return_value="abc123")
        step_result = MagicMock()
        step_result.success = True

        with patch("src.agent.test_runner.launch_chrome", return_value=chrome_proc), \
             patch("src.agent.test_runner.get_ws_url", new_callable=AsyncMock, return_value="ws://localhost:9222"), \
             patch("src.agent.test_runner.CDPConnection", return_value=conn), \
             patch("src.agent.test_runner.PageDomain", return_value=page), \
             patch("src.agent.test_runner.InputDomain", return_value=input_domain), \
             patch("src.agent.test_runner.RuntimeDomain", return_value=runtime), \
             patch("src.agent.test_runner.LLMClient", return_value=llm), \
             patch("src.agent.test_runner._dom_fingerprint", dom_fp_mock), \
             patch("src.agent.test_runner.executor") as mock_executor:

            mock_executor.execute_step = AsyncMock(return_value=step_result)

            runner = DocTestRunner(
                doc_path=str(doc_dir / "docs.md"),
                url="http://localhost:8080",
                output_dir=str(output_dir),
                threshold=0.85,
            )
            report = await runner.run()

        # Missing baseline → similarity=0.0 → DRIFT for all steps
        assert report.overall_status == "DRIFT_DETECTED"
        assert all(r.visual_similarity == 0.0 for r in report.results)


class TestE2EStepException:
    """Scenario: step throws an exception — should be caught as BROKEN."""

    @pytest.mark.asyncio
    async def test_step_exception_caught(self, doc_dir, output_dir):
        chrome_proc, conn, page, input_domain, runtime, llm = _setup_mocks()
        page.screenshot = AsyncMock(return_value=_make_screenshot_b64((30, 60, 180)))
        dom_fp_mock = AsyncMock(return_value="abc123")

        with patch("src.agent.test_runner.launch_chrome", return_value=chrome_proc), \
             patch("src.agent.test_runner.get_ws_url", new_callable=AsyncMock, return_value="ws://localhost:9222"), \
             patch("src.agent.test_runner.CDPConnection", return_value=conn), \
             patch("src.agent.test_runner.PageDomain", return_value=page), \
             patch("src.agent.test_runner.InputDomain", return_value=input_domain), \
             patch("src.agent.test_runner.RuntimeDomain", return_value=runtime), \
             patch("src.agent.test_runner.LLMClient", return_value=llm), \
             patch("src.agent.test_runner._dom_fingerprint", dom_fp_mock), \
             patch("src.agent.test_runner.executor") as mock_executor:

            mock_executor.execute_step = AsyncMock(
                side_effect=RuntimeError("CDP connection lost")
            )

            runner = DocTestRunner(
                doc_path=str(doc_dir / "docs.md"),
                url="http://localhost:8080",
                output_dir=str(output_dir),
                threshold=0.85,
            )
            report = await runner.run()

        # Exception in step → BROKEN
        assert report.overall_status == "BROKEN"
        assert all(r.status == "BROKEN" for r in report.results)
        assert "CDP connection lost" in report.results[0].error
