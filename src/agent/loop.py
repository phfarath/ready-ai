"""
Agentic Loop Orchestrator.

Coordinates the Planner → Executor → Critic pipeline:
1. Launch Chrome → connect CDP
2. Navigate to target URL
3. Planner: goal + DOM → step plan
4. For each step: Executor → screenshot → Annotator → accumulate docs
5. Critic: review → refine if needed
6. Save output
"""

import asyncio
import logging
import signal
from typing import Optional

from ..cdp.browser import launch_chrome, get_ws_url
from ..cdp.connection import CDPConnection
from ..cdp.page import PageDomain
from ..cdp.input import InputDomain
from ..cdp.runtime import RuntimeDomain
from ..llm.client import LLMClient
from ..llm.prompts import ANNOTATOR_PROMPT
from ..docs.renderer import DocRenderer
from ..docs.output import save_docs
from . import planner, executor, critic

logger = logging.getLogger(__name__)


class AgenticLoop:
    """
    Main orchestrator for the agentic documentation generation pipeline.
    """

    def __init__(
        self,
        goal: str,
        url: str,
        model: str = "gpt-4o-mini",
        output_dir: str = "./output",
        port: int = 9222,
        headless: bool = False,
        max_critic_rounds: int = 2,
    ):
        self.goal = goal
        self.url = url
        self.model = model
        self.output_dir = output_dir
        self.port = port
        self.headless = headless
        self.max_critic_rounds = max_critic_rounds

        self._chrome_proc = None
        self._conn: Optional[CDPConnection] = None

    async def run(self) -> str:
        """
        Execute the full agentic documentation pipeline.

        Returns:
            Path to the generated markdown file
        """
        try:
            # 1. Launch Chrome and connect
            await self._setup_browser()

            # 2. Create domain helpers
            page = PageDomain(self._conn)
            input_domain = InputDomain(self._conn)
            runtime = RuntimeDomain(self._conn)
            llm = LLMClient(model=self.model)
            doc = DocRenderer(self.goal)

            # Enable page events
            await page.enable()

            # 3. Navigate to target URL
            logger.info(f"═══ Navigating to: {self.url}")
            await page.navigate(self.url)

            # 4. Plan
            logger.info(f"═══ Planning steps for: {self.goal}")
            dom_html = await page.get_dom_html(max_length=4000)
            elements = await runtime.get_interactive_elements()
            steps = await planner.plan(self.goal, dom_html, elements, llm)

            if not steps:
                raise RuntimeError("Planner returned no steps")

            # 5. Execute each step
            for i, step in enumerate(steps, 1):
                logger.info(f"═══ Step {i}/{len(steps)}: {step}")

                # Get fresh DOM state
                dom_html = await page.get_dom_html(max_length=4000)
                elements = await runtime.get_interactive_elements()

                # Execute
                action_desc = await executor.execute_step(
                    step, dom_html, elements, llm, page, input_domain, runtime
                )
                logger.info(f"    Action: {action_desc}")

                # Wait for UI to settle
                await asyncio.sleep(0.5)

                # Screenshot
                screenshot_b64 = await page.screenshot()

                # Annotate via LLM vision
                annotation = await llm.complete_with_vision(
                    prompt=ANNOTATOR_PROMPT.format(step=step),
                    image_b64=screenshot_b64,
                )

                # Add to doc
                doc.add_step(
                    step_number=i,
                    title=step,
                    screenshot_b64=screenshot_b64,
                    annotation=annotation,
                    action_description=action_desc,
                )

            # 6. Critic review loop
            markdown = doc.render()

            for round_num in range(self.max_critic_rounds):
                logger.info(f"═══ Critic review (round {round_num + 1})")
                feedback = await critic.review(markdown, self.goal, llm)

                logger.info(f"    Score: {feedback.score}/10")
                logger.info(f"    Complete: {feedback.is_complete}")

                if feedback.is_complete and feedback.score >= 7:
                    logger.info("    ✓ Documentation approved by critic")
                    break

                if feedback.suggestions:
                    logger.info(f"    Suggestions: {feedback.suggestions}")

                # For now, just log feedback. Future: re-execute missing steps
                if not feedback.is_complete and feedback.missing_steps:
                    logger.warning(f"    Missing steps: {feedback.missing_steps}")

                # Add critic feedback as a section
                doc.add_critic_notes(feedback.feedback, feedback.suggestions)
                markdown = doc.render()

            # 7. Save output
            output_path = save_docs(markdown, doc.screenshots, self.output_dir)
            logger.info(f"═══ Documentation saved to: {output_path}")
            return output_path

        finally:
            await self._cleanup()

    async def _setup_browser(self) -> None:
        """Launch Chrome and establish CDP connection."""
        logger.info("Launching Chrome...")
        self._chrome_proc = launch_chrome(
            port=self.port,
            headless=self.headless,
        )

        # Wait for Chrome to start and get WS URL
        ws_url = await get_ws_url(port=self.port)

        # Connect via CDP
        self._conn = CDPConnection()
        await self._conn.connect(ws_url)

        # Attach to the first page target
        await self._conn.attach_to_page()

    async def _cleanup(self) -> None:
        """Close connections and kill Chrome process."""
        if self._conn:
            try:
                await self._conn.close()
            except Exception:
                pass

        if self._chrome_proc:
            try:
                self._chrome_proc.terminate()
                self._chrome_proc.wait(timeout=5)
            except Exception:
                try:
                    self._chrome_proc.kill()
                except Exception:
                    pass
            logger.info("Chrome process terminated")
