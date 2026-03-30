"""
Documentation Test Runner — re-executes documented steps to detect UI drift.

This is the core of the "Self-Healing Documentation" feature. It takes a
previously generated docs.md, re-executes each step against the live UI,
compares screenshots with baselines, and produces a DocTestReport.
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..cdp.browser import launch_chrome, get_ws_url
from ..cdp.connection import CDPConnection
from ..cdp.page import PageDomain
from ..cdp.input import InputDomain
from ..cdp.runtime import RuntimeDomain
from ..docs.parser import parse_doc, extract_goal
from ..docs.visual_diff import compare_screenshots
from ..llm.client import LLMClient
from ..llm.prompts import TEST_EXECUTOR_SYSTEM
from . import executor
from .state import DocStepState

logger = logging.getLogger(__name__)


# ─── Result data models ────────────────────────────────────────────────


@dataclass
class StepTestResult:
    """Result of testing a single documented step."""
    step_number: int
    title: str
    status: str  # "PASSED" | "DRIFT" | "BROKEN"
    visual_similarity: float
    dom_changed: bool
    new_screenshot_path: Optional[str] = None
    diff_image_path: Optional[str] = None
    error: str = ""


@dataclass
class DocTestReport:
    """Complete report for a documentation test run."""
    doc_path: str
    url: str
    timestamp: str
    threshold: float
    results: list[StepTestResult] = field(default_factory=list)
    overall_status: str = "PASSED"  # "PASSED" | "DRIFT_DETECTED" | "BROKEN"
    steps_outdated: list[int] = field(default_factory=list)
    steps_broken: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_file(self, path: str | Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    def summary(self) -> str:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.status == "PASSED")
        drift = sum(1 for r in self.results if r.status == "DRIFT")
        broken = sum(1 for r in self.results if r.status == "BROKEN")
        lines = [
            f"Test Report: {self.overall_status}",
            f"  Document: {self.doc_path}",
            f"  URL: {self.url}",
            f"  Threshold: {self.threshold}",
            f"  Results: {passed}/{total} passed, {drift} drift, {broken} broken",
        ]
        if self.steps_outdated:
            lines.append(f"  Outdated steps: {self.steps_outdated}")
        if self.steps_broken:
            lines.append(f"  Broken steps: {self.steps_broken}")
        return "\n".join(lines)


# ─── DOM fingerprint (extracted from AgenticLoop) ──────────────────────


_DOM_FINGERPRINT_JS = """
(() => {
    const selectors = [
        'button', 'input', 'select', 'textarea',
        '[role="tab"]', '[role="menuitem"]',
        '[aria-expanded]', '[aria-selected]', '[data-state]'
    ].join(',');

    const normalize = (value) => (value || '')
        .replace(/\\s+/g, ' ')
        .trim()
        .slice(0, 50);

    const entries = Array.from(document.querySelectorAll(selectors))
        .filter(el => {
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        })
        .map(el => {
            const text = normalize(el.innerText || el.textContent || el.value);
            const state = [
                el.tagName.toLowerCase(),
                el.getAttribute('role') || '',
                text,
                el.getAttribute('aria-expanded') || '',
                el.getAttribute('aria-selected') || '',
                el.getAttribute('data-state') || '',
                el.hasAttribute('disabled') ? 'disabled' : 'enabled',
            ];
            return state.join('|');
        });

    const uniqueSorted = Array.from(new Set(entries)).sort();
    return uniqueSorted.slice(0, 250).join('\\n');
})()
"""


async def _dom_fingerprint(runtime: RuntimeDomain) -> str:
    """Compute a fast hash for SPA-relevant interactive DOM state."""
    try:
        payload = await runtime.evaluate(_DOM_FINGERPRINT_JS)
        payload_str = str(payload) if payload is not None else ""
    except Exception:
        payload_str = ""
    return hashlib.md5(payload_str.encode("utf-8")).hexdigest()


# ─── Test Runner ───────────────────────────────────────────────────────


class DocTestRunner:
    """
    Re-executes documented steps against a live URL and compares results.

    Usage:
        runner = DocTestRunner(
            doc_path="./output/docs.md",
            url="https://app.example.com",
        )
        report = await runner.run()
        print(report.summary())
    """

    def __init__(
        self,
        doc_path: str,
        url: str,
        model: str = "gpt-4o-mini",
        threshold: float = 0.85,
        output_dir: str = "./test-report",
        port: int = 9222,
        headless: bool = True,
        cookies_file: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self.doc_path = doc_path
        self.url = url
        self.model = model
        self.threshold = threshold
        self.output_dir = output_dir
        self.port = port
        self.headless = headless
        self.cookies_file = cookies_file
        self.username = username
        self.password = password

        self._chrome_proc = None
        self._conn: Optional[CDPConnection] = None

    async def run(self) -> DocTestReport:
        """Execute the documentation test and return a DocTestReport."""
        report = DocTestReport(
            doc_path=self.doc_path,
            url=self.url,
            timestamp=datetime.now().isoformat(),
            threshold=self.threshold,
        )

        try:
            # 1. Parse the docs
            steps = parse_doc(self.doc_path)
            logger.info(f"Parsed {len(steps)} steps from {self.doc_path}")

            # 2. Setup output dirs
            output_path = Path(self.output_dir)
            screenshots_dir = output_path / "screenshots"
            diffs_dir = output_path / "diffs"
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            diffs_dir.mkdir(parents=True, exist_ok=True)

            # 3. Launch browser
            self._chrome_proc = await launch_chrome(
                port=self.port, headless=self.headless
            )
            ws_url = await get_ws_url(self.port)
            self._conn = CDPConnection(ws_url)
            await self._conn.connect()

            page = PageDomain(self._conn)
            input_domain = InputDomain(self._conn)
            runtime = RuntimeDomain(self._conn)
            llm = LLMClient(model=self.model)

            await page.enable()

            # 4. Auth if needed
            if self.cookies_file:
                await self._inject_cookies(page)

            # 5. Navigate
            logger.info(f"Navigating to: {self.url}")
            await page.navigate(self.url)

            # 6. Test each step
            for step_state in steps:
                result = await self._test_step(
                    step_state, page, input_domain, runtime, llm,
                    screenshots_dir, diffs_dir,
                )
                report.results.append(result)

                if result.status == "DRIFT":
                    report.steps_outdated.append(result.step_number)
                elif result.status == "BROKEN":
                    report.steps_broken.append(result.step_number)

            # 7. Determine overall status
            if report.steps_broken:
                report.overall_status = "BROKEN"
            elif report.steps_outdated:
                report.overall_status = "DRIFT_DETECTED"
            else:
                report.overall_status = "PASSED"

            # 8. Save report
            report.to_file(output_path / "test_report.json")
            (output_path / "test_summary.txt").write_text(
                report.summary(), encoding="utf-8"
            )
            logger.info(f"Test report saved to {output_path}")

        except Exception as e:
            logger.error(f"Test runner error: {e}")
            report.overall_status = "BROKEN"
            raise
        finally:
            await self._cleanup()

        return report

    async def _test_step(
        self,
        step_state: DocStepState,
        page: PageDomain,
        input_domain: InputDomain,
        runtime: RuntimeDomain,
        llm: LLMClient,
        screenshots_dir: Path,
        diffs_dir: Path,
    ) -> StepTestResult:
        """Test a single documented step."""
        step_num = step_state.number
        logger.info(f"Testing step {step_num}: {step_state.title}")

        try:
            # Get current DOM state
            dom_html = await page.get_dom_html(max_length=4000)
            elements = await runtime.get_interactive_elements()
            pre_fingerprint = await _dom_fingerprint(runtime)

            # Re-execute the step
            result = await executor.execute_step(
                step_state.title,
                dom_html,
                elements,
                llm,
                page,
                input_domain,
                runtime,
            )

            # Wait for UI to settle
            try:
                await page.wait_for_network_idle(timeout=10.0, idle_time=0.5)
            except Exception:
                pass

            post_fingerprint = await _dom_fingerprint(runtime)

            # Take screenshot
            import base64
            screenshot_b64 = await page.screenshot()
            new_screenshot_path = screenshots_dir / f"step_{step_num:02d}.png"
            new_screenshot_path.write_bytes(base64.b64decode(screenshot_b64))

            # Compare with baseline screenshot
            baseline_screenshot = Path(self.doc_path).parent / step_state.screenshot_path
            visual_sim = 1.0
            diff_image_path = None

            if baseline_screenshot.exists():
                diff_result = compare_screenshots(
                    baseline_path=str(baseline_screenshot),
                    current_path=str(new_screenshot_path),
                    output_diff_path=str(diffs_dir / f"diff_step_{step_num:02d}.png"),
                    threshold=self.threshold,
                )
                visual_sim = diff_result.similarity_score
                diff_image_path = diff_result.diff_image_path

            # DOM change detection
            dom_changed = False
            if step_state.baseline_dom_hash:
                dom_changed = post_fingerprint != step_state.baseline_dom_hash

            # Determine step status
            if not result.success:
                status = "BROKEN"
            elif visual_sim < self.threshold:
                status = "DRIFT"
            else:
                status = "PASSED"

            logger.info(
                f"  Step {step_num}: {status} "
                f"(similarity={visual_sim:.3f}, dom_changed={dom_changed})"
            )

            return StepTestResult(
                step_number=step_num,
                title=step_state.title,
                status=status,
                visual_similarity=visual_sim,
                dom_changed=dom_changed,
                new_screenshot_path=str(new_screenshot_path),
                diff_image_path=diff_image_path,
            )

        except Exception as e:
            logger.error(f"  Step {step_num}: BROKEN ({e})")
            return StepTestResult(
                step_number=step_num,
                title=step_state.title,
                status="BROKEN",
                visual_similarity=0.0,
                dom_changed=True,
                error=str(e),
            )

    async def _inject_cookies(self, page: PageDomain) -> None:
        """Inject cookies from file for authentication."""
        cookies_path = Path(self.cookies_file)
        if not cookies_path.exists():
            logger.warning(f"Cookies file not found: {self.cookies_file}")
            return

        cookies = json.loads(cookies_path.read_text(encoding="utf-8"))
        for cookie in cookies:
            await self._conn.send("Network.setCookie", cookie)
        logger.info(f"Injected {len(cookies)} cookies")

    async def _cleanup(self) -> None:
        """Clean up browser resources."""
        if self._conn:
            try:
                await self._conn.close()
            except Exception:
                pass
        if self._chrome_proc:
            try:
                self._chrome_proc.terminate()
                await asyncio.sleep(0.5)
                if self._chrome_proc.returncode is None:
                    self._chrome_proc.kill()
            except Exception:
                pass
