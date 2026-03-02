"""
Agentic Loop Orchestrator.

V2: Full Planner → Executor (with verification) → Critic (with re-execution) pipeline.
Supports authentication via cookies or credentials, and separate annotation model.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from ..cdp.browser import launch_chrome, get_ws_url
from ..cdp.connection import CDPConnection
from ..cdp.page import PageDomain
from ..cdp.input import InputDomain
from ..cdp.runtime import RuntimeDomain
from ..llm.client import LLMClient
from ..llm.prompts import ANNOTATOR_PROMPT, PLANNER_REPLAN_SYSTEM, PLANNER_SUPPLEMENT_SYSTEM
from ..docs.renderer import DocRenderer
from ..docs.output import save_docs
from . import planner, executor, critic

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
    ):
        self.goal = goal
        self.title = title
        self.language = language
        self.url = url
        self.model = model
        self.annotation_model = annotation_model or model
        self.output_dir = output_dir
        self.port = port
        self.headless = headless
        self.max_critic_rounds = max_critic_rounds
        self.cookies_file = cookies_file
        self.username = username
        self.password = password

        self._chrome_proc = None
        self._conn: Optional[CDPConnection] = None
        self._last_url: Optional[str] = None
        self._cursor_task: Optional[asyncio.Task] = None
        self._cursor_moving = False
        
    async def _thinking_cursor_loop(self) -> None:
        """Background task that slightly moves the cursor simulating 'thinking'."""
        import random
        # Start at center
        curr_x, curr_y = 500, 500
        while True:
            try:
                await asyncio.sleep(random.uniform(1.0, 3.0))
                if not self._cursor_moving or not self._conn:
                    continue
                
                # Move occasionally
                curr_x += random.randint(-40, 40)
                curr_y += random.randint(-40, 40)
                
                # Keep within bounds (approximate)
                curr_x = max(10, min(1000, curr_x))
                curr_y = max(10, min(1000, curr_y))
                
                if self._conn:
                    # Ignore errors if page is navigating
                    try:
                        await self._conn.send(
                            "Runtime.evaluate", 
                            {"expression": f"if (window.__browserAutoCursorMove) window.__browserAutoCursorMove({curr_x}, {curr_y})"}
                        )
                    except Exception:
                        pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Cursor loop error: {e}")

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
            annotation_llm = LLMClient(model=self.annotation_model)
            doc = DocRenderer(goal=self.goal, title=self.title, language=self.language)

            # Enable page events
            await page.enable()

            # 3. Inject auth if provided
            if self.cookies_file:
                await self._inject_cookies(page)
            if self.username and self.password:
                await self._handle_login(page, input_domain, runtime, llm)

            # 4. Navigate to target URL
            logger.info(f"═══ Navigating to: {self.url}")
            await page.navigate(self.url)

            # Start thinking cursor
            if not self.headless:
                self._cursor_task = asyncio.create_task(self._thinking_cursor_loop())

            # 5. Plan
            logger.info(f"═══ Planning steps for: {self.goal}")
            self._cursor_moving = True
            dom_html = await page.get_dom_html(max_length=4000)
            elements = await runtime.get_interactive_elements()
            steps = await planner.plan(self.goal, dom_html, elements, llm, language=self.language)
            self._cursor_moving = False

            if not steps:
                raise RuntimeError("Planner returned no steps")

            # 6. Execute each step with verification
            step_results = await self._execute_steps(
                steps, page, input_domain, runtime, llm, annotation_llm, doc
            )

            # 7. Critic review with re-execution loop
            self._cursor_moving = True
            markdown = doc.render()
            await self._critic_loop(
                markdown, page, input_domain, runtime, llm, annotation_llm, doc, step_results
            )
            self._cursor_moving = False

            # 8. Save output
            markdown = doc.render()
            output_path = save_docs(markdown, doc.screenshots, self.output_dir)
            logger.info(f"═══ Documentation saved to: {output_path}")
            return output_path

        finally:
            await self._cleanup()

    async def _execute_steps(
        self,
        steps: list[str],
        page: PageDomain,
        input_domain: InputDomain,
        runtime: RuntimeDomain,
        llm: LLMClient,
        annotation_llm: LLMClient,
        doc: DocRenderer,
        start_number: int = 1,
    ) -> list[executor.StepResult]:
        """Execute a list of steps with verification, screenshots, and annotations."""
        results = []
        self._last_url = None  # reset at start of each execution pass

        step_list = list(steps)
        step_idx = 0
        i = start_number

        while step_idx < len(step_list):
            step = step_list[step_idx]
            logger.info(f"═══ Step {i}: {step}")

            # Get fresh DOM state and current URL for context awareness
            dom_html = await page.get_dom_html(max_length=4000)
            elements = await runtime.get_interactive_elements()
            current_url = await runtime.evaluate("window.location.href")

            # URL drift detection with replanning
            if self._last_url is not None and self._last_url != current_url:
                logger.warning(
                    f"    ⚠ URL changed between steps: {self._last_url} → {current_url}"
                )
                remaining = step_list[step_idx:]
                replanned = await self._replan_remaining(
                    remaining, dom_html, elements, current_url, llm
                )
                if replanned:
                    logger.info(
                        f"    ⟳ Replanned {len(remaining)} remaining steps "
                        f"→ {len(replanned)} adapted steps"
                    )
                    step_list = step_list[:step_idx] + replanned
                    step = step_list[step_idx]

            self._last_url = current_url
            self._cursor_moving = False

            # Execute with verification + retries (with URL context)
            result = await executor.execute_step(
                step, dom_html, elements, llm, page, input_domain, runtime,
                current_url=current_url,
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

            # Highlight the interacted element before screenshot
            last_selector = self._extract_selector(result.action_desc)
            if last_selector:
                await self._highlight_element(runtime, last_selector)

            # Screenshot
            screenshot_b64 = await page.screenshot()

            # Clear highlight after screenshot
            if last_selector:
                await self._clear_highlight(runtime)

            # Annotate via vision LLM (uses annotation_model for cost)
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

            # Mark failed steps in the doc
            title = step if result.success else f"[FAILED] {step}"
            action_desc = result.action_desc
            if result.failure_reason:
                action_desc += f"\n\n**Failure details:** {result.failure_reason}"

            doc.add_step(
                step_number=i,
                title=title,
                screenshot_b64=screenshot_b64,
                annotation=annotation,
                action_description=action_desc,
            )

            results.append(result)
            step_idx += 1
            i += 1
            
            self._cursor_moving = True

        return results

    async def _replan_remaining(
        self,
        remaining_steps: list[str],
        dom_html: str,
        elements: str,
        current_url: str,
        llm: LLMClient,
    ) -> list[str]:
        """
        Replan remaining steps after an unexpected URL change.

        Returns adapted step list, or the original list if replanning fails.
        """
        remaining_context = "\n".join(f"- {s}" for s in remaining_steps)
        user_prompt = (
            f"CURRENT PAGE URL: {current_url}\n\n"
            f"REMAINING PLANNED STEPS (may reference elements from the previous page):\n"
            f"{remaining_context}\n\n"
            f"INTERACTIVE ELEMENTS ON CURRENT PAGE:\n{elements}\n\n"
            f"PAGE HTML (truncated):\n{dom_html[:3000]}\n\n"
            f"The browser navigated to a new page. Adapt the remaining steps to this "
            f"page's context. Preserve steps that still apply, modify steps that "
            f"reference old-page elements, and remove steps that are no longer relevant. "
            f"Output ONLY the numbered list:"
        )
        if self.language:
            user_prompt += f"\nIMPORTANT: Write all output in {self.language}."

        messages = [
            {"role": "system", "content": PLANNER_REPLAN_SYSTEM},
            {"role": "user", "content": user_prompt},
        ]

        try:
            self._cursor_moving = True
            response = await llm.complete(messages)
            self._cursor_moving = False
            new_steps = planner._parse_steps(response)
            return new_steps if new_steps else remaining_steps
        except Exception as e:
            self._cursor_moving = False
            logger.warning(f"    Replanning failed: {e}, continuing with original steps")
            return remaining_steps

    async def _critic_loop(
        self,
        markdown: str,
        page: PageDomain,
        input_domain: InputDomain,
        runtime: RuntimeDomain,
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

            # Critic found missing steps → re-execute them
            if feedback.missing_steps:
                logger.info(f"    ⟳ Re-executing {len(feedback.missing_steps)} missing steps")
                new_results = await self._reexecute_missing_steps(
                    feedback.missing_steps,
                    page, input_domain, runtime, llm, annotation_llm, doc,
                )
                step_results.extend(new_results)
                markdown = doc.render()
                self._cursor_moving = True # Resume moving while reviewing next round
            else:
                # No missing steps, but not approved — add notes and move on
                doc.add_critic_notes(feedback.feedback, feedback.suggestions)
                markdown = doc.render()
                self._cursor_moving = True

    async def _reexecute_missing_steps(
        self,
        missing_steps: list[str],
        page: PageDomain,
        input_domain: InputDomain,
        runtime: RuntimeDomain,
        llm: LLMClient,
        annotation_llm: LLMClient,
        doc: DocRenderer,
    ) -> list[executor.StepResult]:
        """
        Re-plan and execute missing steps identified by the critic.

        Returns:
            List of StepResult from the re-executed steps
        """
        # Get current page context
        dom_html = await page.get_dom_html(max_length=4000)
        elements = await runtime.get_interactive_elements()

        # Ask planner to generate concrete actions for the missing steps
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

        # Execute the new steps starting from the next step number
        next_num = len(doc.steps) + 1
        return await self._execute_steps(
            new_steps, page, input_domain, runtime, llm, annotation_llm, doc,
            start_number=next_num,
        )

    async def _inject_cookies(self, page: PageDomain) -> None:
        """Inject cookies from a JSON file for session authentication."""
        if not self.cookies_file:
            return

        cookie_path = Path(self.cookies_file)
        if not cookie_path.exists():
            logger.error(f"Cookies file not found: {self.cookies_file}")
            return

        try:
            cookies = json.loads(cookie_path.read_text())
            if not isinstance(cookies, list):
                logger.error("Cookies file must contain a JSON array of cookie objects")
                return

            for cookie in cookies:
                # Ensure required fields
                if "name" not in cookie or "value" not in cookie:
                    continue

                # Map common cookie export format to CDP format
                cdp_cookie = {
                    "name": cookie["name"],
                    "value": cookie["value"],
                    "domain": cookie.get("domain", ""),
                    "path": cookie.get("path", "/"),
                    "secure": cookie.get("secure", False),
                    "httpOnly": cookie.get("httpOnly", False),
                }
                if "sameSite" in cookie:
                    cdp_cookie["sameSite"] = cookie["sameSite"]
                if "expirationDate" in cookie:
                    cdp_cookie["expires"] = cookie["expirationDate"]

                await self._conn.send("Network.setCookie", cdp_cookie)

            logger.info(f"Injected {len(cookies)} cookies from {self.cookies_file}")

        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to parse cookies file: {e}")

    async def _handle_login(
        self,
        page: PageDomain,
        input_domain: InputDomain,
        runtime: RuntimeDomain,
        llm: LLMClient,
    ) -> None:
        """
        Automatically detect and fill a login form using provided credentials.
        Navigates to the URL first, detects login fields, fills them, and submits.
        """
        logger.info("═══ Handling authentication...")
        await self._conn.send("Network.enable")
        await page.navigate(self.url)

        # Check if there's a login form on the page
        # Also checks autocomplete attribute for apps using type="text" with autocomplete="email"
        has_login = await runtime.evaluate("""
            (() => {
                const inputs = document.querySelectorAll('input');
                let hasEmail = false, hasPassword = false;
                inputs.forEach(i => {
                    const ac = (i.getAttribute('autocomplete') || '').toLowerCase();
                    if (i.type === 'email' ||
                        (i.type === 'text' && (
                            i.name?.includes('email') || i.name?.includes('user') ||
                            i.placeholder?.toLowerCase().includes('email') ||
                            i.placeholder?.toLowerCase().includes('user') ||
                            ac.includes('email') || ac.includes('username')
                        ))
                    ) hasEmail = true;
                    if (i.type === 'password') hasPassword = true;
                });
                return hasEmail && hasPassword;
            })()
        """)

        if not has_login:
            logger.info("    No login form detected, skipping auth")
            return

        logger.info("    Login form detected, filling credentials")

        # Escape credentials for safe JS interpolation
        safe_username = json.dumps(self.username)
        safe_password = json.dumps(self.password)

        # Find and fill email/username field.
        # Uses the native HTMLInputElement.prototype value setter to bypass React's
        # instance-level property override, so React's synthetic event system correctly
        # picks up the new value and updates the component state.
        email_filled = await runtime.evaluate(f"""
            (() => {{
                const nativeSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                const inputs = document.querySelectorAll('input');
                for (const i of inputs) {{
                    const ac = (i.getAttribute('autocomplete') || '').toLowerCase();
                    if (i.type === 'email' ||
                        (i.type === 'text' && (
                            i.name?.includes('email') || i.name?.includes('user') ||
                            i.placeholder?.toLowerCase().includes('email') ||
                            i.placeholder?.toLowerCase().includes('user') ||
                            ac.includes('email') || ac.includes('username')
                        ))
                    ) {{
                        i.focus();
                        i.select();
                        nativeSetter.call(i, {safe_username});
                        i.dispatchEvent(new InputEvent('input', {{
                            bubbles: true, cancelable: true,
                            inputType: 'insertText', data: {safe_username}
                        }}));
                        i.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        return true;
                    }}
                }}
                return false;
            }})()
        """)

        # Find and fill password field (same native setter approach)
        pass_filled = await runtime.evaluate(f"""
            (() => {{
                const nativeSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                const i = document.querySelector('input[type="password"]');
                if (i) {{
                    i.focus();
                    i.select();
                    nativeSetter.call(i, {safe_password});
                    i.dispatchEvent(new InputEvent('input', {{
                        bubbles: true, cancelable: true,
                        inputType: 'insertText', data: {safe_password}
                    }}));
                    i.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    return true;
                }}
                return false;
            }})()
        """)

        if email_filled and pass_filled:
            # Submit the form
            submitted = await runtime.evaluate("""
                (() => {
                    // Try submit button first
                    const btn = document.querySelector(
                        'button[type="submit"], input[type="submit"], ' +
                        'button:not([type]), [role="button"]'
                    );
                    if (btn) { btn.click(); return 'button'; }
                    // Fallback: submit the form directly
                    const form = document.querySelector('form');
                    if (form) { form.submit(); return 'form'; }
                    return false;
                })()
            """)
            logger.info(f"    Login submitted via: {submitted}")
            # Wait for the auth redirect to fully settle instead of a fixed sleep
            try:
                await self._conn.wait_for_event("Page.loadEventFired", timeout=10.0)
                await asyncio.sleep(1.5)  # extra settle for dynamic content
            except TimeoutError:
                logger.warning("    Auth redirect timed out, continuing anyway")
            logger.info("    Authentication complete")
        else:
            logger.warning("    Could not fill login form automatically")

    async def _setup_browser(self) -> None:
        """Launch Chrome and establish CDP connection."""
        logger.info("Launching Chrome...")
        self._chrome_proc = launch_chrome(
            port=self.port,
            headless=self.headless,
        )

        ws_url = await get_ws_url(port=self.port)

        self._conn = CDPConnection()
        await self._conn.connect(ws_url)
        await self._conn.attach_to_page()

    async def _cleanup(self) -> None:
        """Close connections and kill Chrome process."""
        self._cursor_moving = False
        if self._cursor_task:
            self._cursor_task.cancel()
            try:
                await self._cursor_task
            except asyncio.CancelledError:
                pass
                
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

    @staticmethod
    def _extract_selector(action_desc: str) -> str | None:
        """Extract CSS selector from an action description like 'Clicked element: #btn'."""
        import re
        # Match patterns like "Clicked element: <selector>" or "Scrolled to element: <selector>"
        match = re.search(r"element(?:\s+via\s+\w+\s+fallback)?:\s*(.+?)(?:\n|$)", action_desc)
        if match:
            selector = match.group(1).strip()
            # Don't try to highlight if it's a failure or text-based click
            if selector and not selector.startswith("[Failed") and not selector.startswith("[Error"):
                return selector
        return None

    @staticmethod
    async def _highlight_element(runtime: RuntimeDomain, selector: str) -> None:
        """Draw a visual highlight (red border + semi-transparent overlay) on an element and move the global cursor to it."""
        safe_selector = json.dumps(selector)
        try:
            await runtime.evaluate(f"""
                (() => {{
                    const el = document.querySelector({safe_selector});
                    if (!el) return;
                    
                    // Highlight the element
                    el.dataset._prevOutline = el.style.outline || '';
                    el.dataset._prevOutlineOffset = el.style.outlineOffset || '';
                    el.dataset._prevBoxShadow = el.style.boxShadow || '';
                    el.style.outline = '3px solid #FF0000';
                    el.style.outlineOffset = '2px';
                    el.style.boxShadow = '0 0 0 4px rgba(255, 0, 0, 0.25)';
                    el.setAttribute('data-browser-auto-highlight', 'true');
                    
                    // Move the global cursor to the element for the screenshot
                    if (window.__browserAutoCursorMove) {{
                        const rect = el.getBoundingClientRect();
                        const centerX = rect.left + rect.width / 2;
                        const centerY = rect.top + rect.height / 2;
                        window.__browserAutoCursorMove(centerX, centerY);
                    }}
                }})()
            """)
        except Exception:
            pass  # Non-critical — screenshot still works without highlight

    @staticmethod
    async def _clear_highlight(runtime: RuntimeDomain, selector: str = "") -> None:
        """Remove visual highlight from the previously highlighted element."""
        try:
            await runtime.evaluate("""
                (() => {
                    const el = document.querySelector('[data-browser-auto-highlight]');
                    if (el) {
                        el.style.outline = el.dataset._prevOutline || '';
                        el.style.outlineOffset = el.dataset._prevOutlineOffset || '';
                        el.style.boxShadow = el.dataset._prevBoxShadow || '';
                        el.removeAttribute('data-browser-auto-highlight');
                        delete el.dataset._prevOutline;
                        delete el.dataset._prevOutlineOffset;
                        delete el.dataset._prevBoxShadow;
                    }
                })()
            """)
        except Exception:
            pass
