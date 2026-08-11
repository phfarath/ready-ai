"""
Tests for Chrome PID registration in DocTestRunner (VAL-ROB-007).

DocTestRunner.run() launches Chrome directly via launch_chrome but must
also register the PID with the atexit cleanup system so orphan Chrome
processes are killed on unexpected shutdowns. After teardown the PID
must be unregistered.
"""
# ruff: noqa: E402

import base64
import shutil
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Restore real PIL if another test module mocked it
if "PIL" in sys.modules and hasattr(sys.modules["PIL"], "_mock_name"):
    for k in list(sys.modules):
        if k.startswith("PIL"):
            del sys.modules[k]

from PIL import Image

Image.init()  # register format plugins on Windows

from src.agent.test_runner import DocTestRunner


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sample_doc"


def _file_to_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def _make_screenshot_b64(color: tuple[int, int, int] = (30, 60, 180)) -> str:
    img = Image.new("RGB", (200, 150), color)
    buf = BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def doc_dir(tmp_path):
    """Copy fixture docs to a temp dir so tests don't pollute fixtures."""
    dest = tmp_path / "sample_doc"
    shutil.copytree(FIXTURE_DIR, dest)
    return dest


@pytest.fixture
def output_dir(tmp_path):
    out = tmp_path / "test-report"
    out.mkdir()
    return out


def _build_mocks(pid: int = 99999):
    """Create the CDP/browser mocks needed by DocTestRunner.run()."""
    chrome_proc = MagicMock()
    chrome_proc.pid = pid
    chrome_proc.terminate = MagicMock()
    chrome_proc.kill = MagicMock()
    chrome_proc.poll = MagicMock(return_value=0)
    chrome_proc.returncode = 0

    conn = AsyncMock()
    page = AsyncMock()
    input_domain = AsyncMock()
    runtime = AsyncMock()
    runtime.get_interactive_elements = AsyncMock(
        return_value='[{"tag": "button", "id": "login-btn", "text": "Login"}]'
    )
    llm = AsyncMock()

    step_result = MagicMock()
    step_result.success = True

    return chrome_proc, conn, page, input_domain, runtime, llm, step_result


class TestRunnerRegistersChromePid:
    """VAL-ROB-007: DocTestRunner registers Chrome PID for atexit cleanup."""

    @pytest.mark.asyncio
    async def test_run_registers_chrome_pid_after_launch(
        self, doc_dir, output_dir
    ):
        """After launch_chrome, _register_chrome_pid is called with the PID."""
        chrome_proc, conn, page, input_domain, runtime, llm, step_result = (
            _build_mocks(pid=99999)
        )

        # Return exact baseline bytes so all steps pass
        step_screenshots = [
            _file_to_b64(doc_dir / "screenshots" / "step_01.png"),
            _file_to_b64(doc_dir / "screenshots" / "step_02.png"),
        ]
        page.screenshot = AsyncMock(side_effect=step_screenshots)
        dom_fp_mock = AsyncMock(return_value="abc123")

        register_mock = MagicMock()
        unregister_mock = MagicMock()

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
             patch("src.agent.test_runner._dom_fingerprint", dom_fp_mock), \
             patch("src.agent.test_runner.executor") as mock_executor, \
             patch(
                 "src.agent.test_runner._register_chrome_pid",
                 register_mock,
                 create=True,
             ), \
             patch(
                 "src.agent.test_runner._unregister_chrome_pid",
                 unregister_mock,
                 create=True,
             ):

            mock_executor.execute_step = AsyncMock(return_value=step_result)

            runner = DocTestRunner(
                doc_path=str(doc_dir / "docs.md"),
                url="http://localhost:8080",
                output_dir=str(output_dir),
            )
            await runner.run()

        register_mock.assert_called_once_with(99999)

    @pytest.mark.asyncio
    async def test_run_unregisters_chrome_pid_on_cleanup(
        self, doc_dir, output_dir
    ):
        """After teardown, _unregister_chrome_pid is called with the PID."""
        chrome_proc, conn, page, input_domain, runtime, llm, step_result = (
            _build_mocks(pid=88888)
        )

        step_screenshots = [
            _file_to_b64(doc_dir / "screenshots" / "step_01.png"),
            _file_to_b64(doc_dir / "screenshots" / "step_02.png"),
        ]
        page.screenshot = AsyncMock(side_effect=step_screenshots)
        dom_fp_mock = AsyncMock(return_value="abc123")

        register_mock = MagicMock()
        unregister_mock = MagicMock()

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
             patch("src.agent.test_runner._dom_fingerprint", dom_fp_mock), \
             patch("src.agent.test_runner.executor") as mock_executor, \
             patch(
                 "src.agent.test_runner._register_chrome_pid",
                 register_mock,
                 create=True,
             ), \
             patch(
                 "src.agent.test_runner._unregister_chrome_pid",
                 unregister_mock,
                 create=True,
             ):

            mock_executor.execute_step = AsyncMock(return_value=step_result)

            runner = DocTestRunner(
                doc_path=str(doc_dir / "docs.md"),
                url="http://localhost:8080",
                output_dir=str(output_dir),
            )
            await runner.run()

        unregister_mock.assert_called_once_with(88888)

    @pytest.mark.asyncio
    async def test_pid_not_registered_when_chrome_not_launched(
        self, doc_dir, output_dir
    ):
        """If doc file is missing, Chrome is never launched so no PID is registered."""
        register_mock = MagicMock()
        unregister_mock = MagicMock()

        with patch(
            "src.agent.test_runner.launch_chrome",
            return_value=MagicMock(pid=77777),
        ), \
             patch(
                 "src.agent.test_runner._register_chrome_pid",
                 register_mock,
                 create=True,
             ), \
             patch(
                 "src.agent.test_runner._unregister_chrome_pid",
                 unregister_mock,
                 create=True,
             ):

            runner = DocTestRunner(
                doc_path=str(doc_dir / "nonexistent.md"),
                url="http://localhost:8080",
                output_dir=str(output_dir),
            )
            with pytest.raises(FileNotFoundError):
                await runner.run()

        register_mock.assert_not_called()
        unregister_mock.assert_not_called()
