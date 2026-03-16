"""
Agentic Loop Orchestrator.

V2: Full Planner → Executor (with verification) → Critic (with re-execution) pipeline.
Supports authentication via cookies or credentials, and separate annotation model.
"""

import asyncio
import logging
import websockets
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from ..llm.client import LLMClient
from ..llm.prompts import ANNOTATOR_PROMPT, PLANNER_SUPPLEMENT_SYSTEM
from ..docs.renderer import DocRenderer
from ..docs.output import save_docs
from . import planner, executor, critic, recovery
from .cursor import CursorAnimator, extract_selector
from .browser_session import BrowserSession
from .state import RunState, DocStepState

logger = logging.getLogger(__name__)


class AgenticLoop:
    """
    Main orchestrator for the agentic documentation generation pipeline.

    V2 enhancements:
    - Post-action verification in executor (StepResult with retries)
    - Critic can trigger re-execution of missing steps
    - Authentication support (cookies file or username/password)
    - Separate annotation model for cost optimization
    """

    def __init__(
        self,
        goal: str,
        url: str,
        model: str = "gpt-4o-mini",
        annotation_model: Optional[str] = None,
        output_dir: str = "./output",
        port: int = 9222,
        headless: bool = False,
        max_critic_rounds: int = 2,
        cookies_file: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        title: Optional[str] = None,
        language: Optional[str] = None,
        run_id: str = "local_run",
        resume_from: Optional[str] = None,
        plan_only: bool = False,
    ):
        self.run_id = run_id
        self.resume_from = resume_from
        self.plan_only = plan_only
        self.goal = goal
        self.title = title
        self.language = language
        self.url = url
        self.model = model
        self.annotation_model = annotation_model or model
        self.output_dir = output_dir
        self.headless = headless
        self.max_critic_rounds = max_critic_rounds

        self._session = BrowserSession(
            port=port,
            headless=headless,
            cookies_file=cookies_file,
            username=username,
            password=password,
        )
        self._cursor = CursorAnimator()
        self._last_url: Optional[str] = None
        self._max_replans_per_step = 2

        # Checkpointing state
        self._state_path = Path(self.output_dir) / f"{self.run_id}_state.json"

        if self.resume_from and Path(self.resume_from).exists():
            self._state = RunState.from_file(self.resume_from)
            if self._state:
                logger.info(f"Resuming run '{self._state.run_id}' from state file {self.resume_from}")
            else:
                self._state = RunState(run_id=self.run_id, goal=self.goal, url=self.url)
        else:
            self._state = RunState(run_id=self.run_id, goal=self.goal, url=self.url)

    def _save_checkpoint(self, status: Optional[str] = None) -> None:
        """Write current execution state to disk."""
        if status:
            self._state.status = status

        if self._last_url:
            self._state.last_known_url = self._last_url

        if getattr(self, 'doc', None):
            self._state.doc_steps = [
                DocStepState(
                    number=s.step_number,
                    title=s.title,
                    action_description=s.action_description,
                    annotation=s.annotation,
                    screenshot_path=f"step_{s.step_number:02d}.png",
                    status=s.status,
                    status_reason=s.status_reason,
                ) for s in self.doc.steps
            ]

        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state.to_file(self._state_path)

    async def run(self) -> str:
        """
        Execute the full agentic documentation pipeline.

        Returns:
            Path to the generated markdown file
        """
        try:
            # 1. Launch Chrome and connect
            await self._session.setup()

            # 2. Create domain helpers
            llm = LLMClient(model=self.model)
            annotation_llm = LLMClient(model=self.annotation_model)
            doc = DocRenderer(goal=self.goal, title=self.title, language=self.language)
            self.llm = llm
            self.annotation_llm = annotation_llm
            self.doc = doc

            # Enable page events
            await self._session.page.enable()

            # 3. Inject auth if provided
            if self._session.cookies_file:
                await self._session.inject_cookies()
            if self._session.username and self._session.password:
                await self._session.page.navigate(self.url)
                await self._session.handle_login(llm)

            # 4. Navigate to target URL
            logger.info(f"═══ Navigating to: {self.url}")
            await self._session.page.navigate(self.url)

            # Start thinking cursor
            if not self.headless:
                self._cursor.start(self._session.conn)

            # 5. Plan (or load from checkpoint)
            steps = await self._resolve_steps(llm, doc)

            if not steps:
                raise RuntimeError("Planner returned no steps")

            if self.plan_only:
                self._save_checkpoint("PLANNED")
                self._log_plan(steps)
                return str(self._state_path)

            # 6. Execute each step with verification
            step_results = await self._execute_steps(steps, llm, annotation_llm, doc)

            # 7. Critic review with re-execution loop
            self._save_checkpoint("CRITIQUE")
            self._cursor.moving = True
            markdown = doc.render()
            await self._critic_loop(markdown, llm, annotation_llm, doc, step_results)
            self._cursor.moving = False

            # 8. Save output
            self._save_checkpoint("FINISHED")
            markdown = doc.render()
            output_path = save_docs(markdown, doc.screenshots, self.output_dir)
            logger.info(f"═══ Documentation saved to: {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            self._save_checkpoint("FAILED")
            raise
        finally:
            await self._cursor.stop()
            await self._session.teardown()

    def _restore_doc_from_state(self, doc: DocRenderer) -> None:
        """Restore rendered step metadata from checkpoint state."""
        if not self._state.doc_steps:
            return
        for ds in self._state.doc_steps:
            doc.add_step(
                step_number=ds.number,
                title=ds.title,
                screenshot_b64="",
                annotation=ds.annotation,
                action_description=ds.action_description,
                status=ds.status,
                status_reason=ds.status_reason,
            )

    def _log_plan(self, steps: list[str]) -> None:
        """Log a numbered plan for plan-only mode."""
        logger.info("═══ Planned steps")
        for index, step in enumerate(steps, 1):
            logger.info("    %s. %s", index, step)

    async def _resolve_steps(self, llm: LLMClient, doc: DocRenderer) -> list[str]:
        """Load a saved plan or generate a new one for the current page state."""
        has_saved_plan = bool(self._state.planned_steps)
        should_resume_plan = has_saved_plan and (
            self.plan_only or self._state.current_step_index < len(self._state.planned_steps)
        )

        if should_resume_plan:
            logger.info(
                "═══ Resuming planned steps from checkpoint (%s/%s)",
                self._state.current_step_index,
                len(self._state.planned_steps),
            )
            self._restore_doc_from_state(doc)
            return self._state.planned_steps

        logger.info("═══ Planning steps for: %s", self.goal)
        self._save_checkpoint("PLANNING")
        self._cursor.moving = True
        page = self._session.page
        runtime = self._session.runtime
        dom_html = await page.get_dom_html(max_length=4000)
        elements = await runtime.get_interactive_elements()
        steps = await planner.plan(self.goal, dom_html, elements, llm, language=self.language)
        self._cursor.moving = False

        self._state.planned_steps = steps
        self._state.current_step_index = 0
        self._state.executed_results = []
        self._state.doc_steps = []
        self._save_checkpoint("PLANNED" if self.plan_only else "EXECUTING")
        return steps

    async def _get_page_context(self, max_length: int = 4000) -> tuple[str, str, str]:
        """Get DOM HTML, interactive elements, and current URL in one call."""
        page = self._session.page
        runtime = self._session.runtime
        dom_html = await page.get_dom_html(max_length=max_length)
        elements = await runtime.get_interactive_elements()
        url = await runtime.evaluate("window.location.href")
        return dom_html, elements, url

    @staticmethod
    def _format_step_action_details(result: executor.StepResult) -> str:
        details = result.action_desc
        if result.failure_reason:
            details += f"\n\n**Failure details:** {result.failure_reason}"
        return details

    async def _execute_steps(
        self,
        steps: list[str],
        llm: LLMClient,
        annotation_llm: LLMClient,
        doc: DocRenderer,
        start_number: int = 1,
    ) -> list[executor.StepResult]:
        """Execute a list of steps with verification, screenshots, and annotations."""
        results = []
        self._last_url = None

        page = self._session.page
        input_domain = self._session.input_domain
        runtime = self._session.runtime

        step_list = list(steps)
        step_idx = self._state.current_step_index
        i = start_number + step_idx
        crashes = 0
        MAX_CRASHES = 3
        replan_attempts_by_index: dict[int, int] = {}

        while step_idx < len(step_list):
            try:
                step = step_list[step_idx]
                logger.info(f"═══ Step {i}: {step}")

                # Get fresh DOM state and current URL
                dom_html = await page.get_dom_html(max_length=4000)
                elements = await runtime.get_interactive_elements()
                pre_url = await runtime.evaluate("window.location.href")
                pre_fingerprint = await recovery.dom_fingerprint(runtime)

                # URL drift detection with replanning
                if self._last_url is not None and self._last_url != pre_url:
                    logger.warning(
                        f"    ⚠ URL changed between steps: {self._last_url} → {pre_url}"
                    )
                    remaining = step_list[step_idx:]
                    replanned = await recovery.replan_remaining(
                        remaining, dom_html, elements, pre_url, llm,
                        language=self.language,
                    )
                    if replanned:
                        logger.info(
                            f"    ⟳ Replanned {len(remaining)} remaining steps "
                            f"→ {len(replanned)} adapted steps"
                        )
                        step_list = step_list[:step_idx] + replanned
                        self._state.planned_steps = step_list
                        self._save_checkpoint("EXECUTING")
                        step = step_list[step_idx]

                self._last_url = pre_url
                self._cursor.moving = False

                # Execute with verification + retries
                result = await executor.execute_step(
                    step, dom_html, elements, llm, page, input_domain, runtime,
                    current_url=pre_url,
                )

                logger.info(
                    f"    {'✓' if result.success else '✗'} {result.action_desc} "
                    f"(attempts: {result.attempts})"
                )

                # Wait for UI and network to settle
                try:
                    await page.wait_for_network_idle(timeout=10.0, idle_time=0.5)
                except Exception as e:
                    logger.debug(f"Wait for network idle failed/timed out: {e}")

                post_url = await runtime.evaluate("window.location.href")
                post_fingerprint = await recovery.dom_fingerprint(runtime)

                if not result.success:
                    result, step, replan_attempts = await recovery.recover_failed_step(
                        step=step,
                        result=result,
                        pre_url=pre_url,
                        post_url=post_url,
                        pre_fingerprint=pre_fingerprint,
                        post_fingerprint=post_fingerprint,
                        page=page,
                        input_domain=input_domain,
                        runtime=runtime,
                        llm=llm,
                        replan_attempts=replan_attempts_by_index.get(step_idx, 0),
                        max_replans_per_step=self._max_replans_per_step,
                        language=self.language,
                    )
                    replan_attempts_by_index[step_idx] = replan_attempts
                    step_list[step_idx] = step
                    self._state.planned_steps = step_list
                    self._save_checkpoint("EXECUTING")

                # Highlight the interacted element before screenshot
                last_selector = extract_selector(result.action_desc)
                if last_selector:
                    await CursorAnimator.highlight_element(runtime, last_selector)

                # Screenshot
                screenshot_b64 = await page.screenshot()

                # Clear highlight after screenshot
                if last_selector:
                    await CursorAnimator.clear_highlight(runtime)

                # Annotate via vision LLM
                language_instruction = (
                    f"Write in {self.language}"
                    if self.language
                    else "Write in the same language as the GOAL, not the UI text visible in the screenshot"
                )
                annotation = await annotation_llm.complete_with_vision(
                    prompt=ANNOTATOR_PROMPT.format(
                        language_instruction=language_instruction,
                        goal=self.goal,
                        step=step,
                    ),
                    image_b64=screenshot_b64,
                )

                doc.add_step(
                    step_number=i,
                    title=step,
                    screenshot_b64=screenshot_b64,
                    annotation=annotation,
                    action_description=self._format_step_action_details(result),
                    status=result.status or "completed",
                    status_reason=result.failure_reason,
                )

                self._state.executed_results.append(asdict(result))
                results.append(result)
                step_idx += 1
                self._state.current_step_index = step_idx
                self._save_checkpoint("EXECUTING")

                i += 1
                self._cursor.moving = True

            except websockets.exceptions.ConnectionClosed:
                crashes += 1
                if crashes > MAX_CRASHES:
                    logger.error(f"Exceeded maximum global crashes ({MAX_CRASHES}). Aborting pipeline.")
                    raise

                resume_url = self._last_url or self.url
                await self._session.recover(resume_url)
                page = self._session.page
                input_domain = self._session.input_domain
                runtime = self._session.runtime

        return results

    async def _critic_loop(
        self,
        markdown: str,
        llm: LLMClient,
        annotation_llm: LLMClient,
        doc: DocRenderer,
        step_results: list[executor.StepResult],
    ) -> None:
        """
        Critic review loop with re-execution of missing steps.

        If the critic identifies missing steps, they are sent back to the
        Planner for a sub-plan, then executed and appended to the documentation.
        """
        for round_num in range(self.max_critic_rounds):
            logger.info(f"═══ Critic review (round {round_num + 1})")
            feedback = await critic.review(markdown, self.goal, llm)

            logger.info(f"    Score: {feedback.score}/10")
            logger.info(f"    Complete: {feedback.is_complete}")

            if feedback.is_complete and feedback.score >= 7:
                logger.info("    ✓ Documentation approved by critic")
                return

            if feedback.suggestions:
                logger.info(f"    Suggestions: {feedback.suggestions}")

            if feedback.missing_steps:
                logger.info(f"    ⟳ Re-executing {len(feedback.missing_steps)} missing steps")
                new_results = await self._reexecute_missing_steps(
                    feedback.missing_steps, llm, annotation_llm, doc,
                )
                step_results.extend(new_results)
                markdown = doc.render()
                self._cursor.moving = True
            else:
                doc.add_critic_notes(feedback.feedback, feedback.suggestions)
                markdown = doc.render()
                self._cursor.moving = True

    async def _reexecute_missing_steps(
        self,
        missing_steps: list[str],
        llm: LLMClient,
        annotation_llm: LLMClient,
        doc: DocRenderer,
    ) -> list[executor.StepResult]:
        """
        Re-plan and execute missing steps identified by the critic.

        Returns:
            List of StepResult from the re-executed steps
        """
        page = self._session.page
        runtime = self._session.runtime

        dom_html = await page.get_dom_html(max_length=4000)
        elements = await runtime.get_interactive_elements()

        missing_context = "\n".join(f"- {s}" for s in missing_steps)
        supplement_prompt = (
            f"MISSING STEPS TO COVER:\n{missing_context}\n\n"
            f"INTERACTIVE ELEMENTS:\n{elements}\n\n"
            f"PAGE HTML (truncated):\n{dom_html[:3000]}\n\n"
            f"Generate the numbered steps to cover these gaps:"
        )
        if self.language:
            supplement_prompt += f"\nIMPORTANT: Write all output in {self.language}."

        messages = [
            {"role": "system", "content": PLANNER_SUPPLEMENT_SYSTEM},
            {"role": "user", "content": supplement_prompt},
        ]

        response = await llm.complete(messages)
        new_steps = planner._parse_steps(response)

        if not new_steps:
            logger.warning("    Planner returned no supplement steps")
            return []

        logger.info(f"    Planner generated {len(new_steps)} supplement steps")

        next_num = len(doc.steps) + 1
        return await self._execute_steps(
            new_steps, llm, annotation_llm, doc,
            start_number=next_num,
        )
