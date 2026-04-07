"""
Executor Agent — takes a step and page context, produces and executes a CDP action.

V2: With post-action verification, retry loop, and fallback strategies.
Each step gets up to MAX_RETRIES attempts. After each action, the DOM is
compared before/after to detect if anything actually changed.
"""

import asyncio
import hashlib
import json
import logging
import re
import websockets
from dataclasses import dataclass
from typing import Optional

from ..cdp.input import InputDomain
from ..cdp.page import PageDomain
from ..cdp.runtime import RuntimeDomain
from ..llm.client import LLMClient
from ..llm.prompts import EXECUTOR_SYSTEM, EXECUTOR_RETRY_SYSTEM

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

# JavaScript helper: querySelector that pierces shadow DOM boundaries.
# Embed with: f"(() => {{ {_PIERCE_JS} const el = pierce(document, {safe_sel}); ... }})()"
_PIERCE_JS = (
    "function pierce(root,sel){"
    "let el;try{el=root.querySelector(sel);}catch(e){}"
    "if(el)return el;"
    "for(const n of root.querySelectorAll('*')){"
    "if(n.shadowRoot){el=pierce(n.shadowRoot,sel);if(el)return el;}}"
    "return null;}"
)


@dataclass
class StepResult:
    """Result of executing a single step."""
    action_desc: str
    success: bool
    retry_needed: bool
    attempts: int = 1
    failure_reason: str = ""
    status: str = ""


async def execute_step(
    step: str,
    dom_html: str,
    interactive_elements: str,
    llm: LLMClient,
    page: PageDomain,
    input_domain: InputDomain,
    runtime: RuntimeDomain,
    previous_failures: list[str] | None = None,
    current_url: str | None = None,
) -> StepResult:
    """
    Execute a single documentation step with post-action verification and retries.

    After each action, compares DOM state before/after. If nothing changed
    and the action was supposed to modify the page, retries with additional
    context about the failure. Max MAX_RETRIES attempts per step.

    Args:
        step: The step description to execute
        dom_html: Current page DOM HTML
        interactive_elements: JSON list of interactive elements
        llm: LLM client
        page: CDP Page domain
        input_domain: CDP Input domain
        runtime: CDP Runtime domain
        previous_failures: List of previous failure descriptions for retry context
        current_url: Current page URL for context awareness

    Returns:
        StepResult with success status and action description
    """
    failures = list(previous_failures or [])
    last_action_desc = ""

    for attempt in range(1, MAX_RETRIES + 1):
        # Capture DOM state BEFORE action
        text_before = await runtime.get_state_fingerprint()
        url_before = await runtime.evaluate("window.location.href")

        # Build prompt — include failure context on retries
        action = await _get_action(
            step, dom_html, interactive_elements, llm, failures, current_url
        )

        if action is None:
            logger.warning(f"  [Attempt {attempt}] Could not parse action for: {step}")
            failures.append("LLM returned unparseable action JSON")
            continue

        # Execute the action
        action_desc = await _dispatch_action(action, page, input_domain, runtime)
        last_action_desc = action_desc

        # Actions that don't change DOM are always "successful"
        action_type = action.get("action", "observe")
        if action_type in ("observe", "wait"):
            return StepResult(
                action_desc=action_desc,
                success=True,
                retry_needed=False,
                attempts=attempt,
            )

        # Wait for UI to settle. Explicit navigation barrier handles hard
        # navigations (process swap / app router transition) so the post-action
        # fingerprint runs against a live execution context, not a dead one.
        try:
            await page.wait_for_navigation_settled(timeout=10.0)
        except Exception:
            await asyncio.sleep(0.5)  # generic fallback

        # Capture DOM state AFTER action — bounded so a dead/dying execution
        # context can never hang the whole pipeline. On timeout we treat the
        # action as having navigated (forcing url_changed=True), which is the
        # only way Runtime.evaluate could legitimately hang here anyway.
        try:
            text_after = await asyncio.wait_for(
                runtime.get_state_fingerprint(), timeout=5.0
            )
            url_after = await asyncio.wait_for(
                runtime.evaluate("window.location.href"), timeout=3.0
            )
        except asyncio.TimeoutError:
            logger.warning(
                "  Post-action state capture timed out — treating as navigation"
            )
            text_after = "<post-action-capture-timeout>"
            url_after = url_before + "#__nav__"
        except Exception as e:
            msg = str(e).lower()
            # Execution context destroyed mid-call is a navigation symptom —
            # treat like a timeout. Anything else is a real failure and must
            # stay retryable so the step isn't falsely reported as success.
            if (
                "execution context" in msg
                or "context was destroyed" in msg
                or "target closed" in msg
                or "no such execution context" in msg
            ):
                logger.warning(
                    f"  Post-action capture hit destroyed context ({e}) "
                    f"— treating as navigation"
                )
                text_after = "<post-action-capture-context-destroyed>"
                url_after = url_before + "#__nav__"
            else:
                raise

        # Check if something changed
        url_changed = url_before != url_after
        text_changed = (
            hashlib.md5(text_before.encode()).hexdigest()
            != hashlib.md5(text_after.encode()).hexdigest()
        )
        changed = url_changed or text_changed

        if changed:
            logger.info(f"  [Attempt {attempt}] ✓ Action verified — DOM changed")
            return StepResult(
                action_desc=action_desc,
                success=True,
                retry_needed=False,
                attempts=attempt,
            )

        # Action didn't change anything — might have failed silently
        if "[Failed]" in action_desc or "[Error]" in action_desc:
            failure_msg = f"Attempt {attempt}: {action_desc}"
            failures.append(failure_msg)
            logger.warning(f"  [Attempt {attempt}] ✗ Action failed: {action_desc}")
        else:
            failure_msg = (
                f"Attempt {attempt}: Executed '{action_desc}' but DOM did not change. "
                f"Element may be out of viewport, covered by a modal, or the selector is wrong."
            )
            failures.append(failure_msg)
            logger.warning(f"  [Attempt {attempt}] ✗ DOM unchanged after action")

        # Refresh DOM state for next attempt
        dom_html = await page.get_dom_html(max_length=4000)
        interactive_elements = await runtime.get_interactive_elements()

        # Try fallback: scroll element into view before retrying click
        if action_type == "click" and attempt < MAX_RETRIES:
            selector = action.get("selector", "")
            if selector:
                await _try_scroll_into_view(selector, runtime)

    # All retries exhausted
    logger.error(f"  [FAILED] Step after {MAX_RETRIES} attempts: {step}")
    return StepResult(
        action_desc=f"[FAILED after {MAX_RETRIES} attempts] {last_action_desc}",
        success=False,
        retry_needed=False,
        attempts=MAX_RETRIES,
        failure_reason="; ".join(failures),
    )


async def _get_action(
    step: str,
    dom_html: str,
    interactive_elements: str,
    llm: LLMClient,
    failures: list[str],
    current_url: str | None = None,
) -> dict | None:
    """Ask LLM for the action to execute, including retry context."""
    url_context = f"CURRENT PAGE URL: {current_url}\n\n" if current_url else ""

    if failures:
        # Use retry prompt with failure context
        failure_context = "\n".join(f"  - {f}" for f in failures)
        user_prompt = (
            f"{url_context}"
            f"STEP TO EXECUTE: {step}\n\n"
            f"PREVIOUS ATTEMPTS FAILED:\n{failure_context}\n\n"
            f"Try a DIFFERENT approach. Consider:\n"
            f"- Use a different selector (try aria-label, data-testid, role, or XPath-style)\n"
            f"- The element might need scrolling into view first\n"
            f"- Try a JavaScript click as fallback\n\n"
            f"INTERACTIVE ELEMENTS:\n{interactive_elements}\n\n"
            f"PAGE HTML (truncated):\n{dom_html[:3000]}\n\n"
            f"Output the JSON action:"
        )
        system = EXECUTOR_RETRY_SYSTEM
    else:
        user_prompt = (
            f"{url_context}"
            f"STEP TO EXECUTE: {step}\n\n"
            f"INTERACTIVE ELEMENTS:\n{interactive_elements}\n\n"
            f"PAGE HTML (truncated):\n{dom_html[:3000]}\n\n"
            f"Output the JSON action to execute this step:"
        )
        system = EXECUTOR_SYSTEM

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_prompt},
    ]

    response = await llm.complete(messages, json_mode=True, role="executor")
    return _parse_action(response)


_TEXT_PSEUDO_RE = re.compile(
    r"""(?ix)
    (?:
      \[\s*text\s*=\s*['"]([^'"]+)['"]\s*\]   # [text='X']
      | :contains\(\s*['"]([^'"]+)['"]\s*\)   # :contains('X')
      | :has-text\(\s*['"]([^'"]+)['"]\s*\)   # :has-text('X')
    )
    """
)


def _extract_text_pseudo(selector: str) -> Optional[str]:
    """
    Detect non-CSS text pseudo-selectors that LLMs love to emit
    (`[text='X']`, `:contains('X')`, `:has-text('X')`) and return the
    inner text. Returns None if the selector is valid CSS.
    """
    if not selector:
        return None
    match = _TEXT_PSEUDO_RE.search(selector)
    if not match:
        return None
    return next((g for g in match.groups() if g), None)


async def _try_scroll_into_view(selector: str, runtime: RuntimeDomain) -> None:
    """Attempt to scroll an element into view before retrying a click."""
    try:
        safe_sel = json.dumps(selector)
        js = (
            f"(() => {{ {_PIERCE_JS} "
            f"pierce(document, {safe_sel})?.scrollIntoView({{behavior: 'smooth', block: 'center'}}); }})()"
        )
        await runtime.evaluate(js)
        await asyncio.sleep(0.1)  # tiny delay for UI to paint
        logger.debug(f"  Scrolled '{selector}' into view for retry")
    except Exception:
        pass


def _parse_action(response: str) -> dict | None:
    """Parse JSON action from LLM response."""
    try:
        return json.loads(response.strip())
    except json.JSONDecodeError:
        pass

    # Try to extract JSON from markdown code block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find any JSON object
    match = re.search(r"\{[^{}]+\}", response)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    logger.error(f"Failed to parse action JSON: {response[:200]}")
    return None


async def _dispatch_action(
    action: dict,
    page: PageDomain,
    input_domain: InputDomain,
    runtime: RuntimeDomain,
) -> str:
    """
    Execute a parsed action dict via CDP.

    Returns:
        Human-readable description of what was done
    """
    action_type = action.get("action", "observe")

    try:
        if action_type == "click":
            selector = action["selector"]
            # LLMs frequently emit non-CSS pseudo-selectors like
            # `button[type='submit'][text='Entrar']`, `:contains("X")`, or
            # `:has-text("X")`. These never match. Detect them, extract the
            # text, and re-dispatch as click_text.
            text_match = _extract_text_pseudo(selector)
            if text_match is not None:
                logger.info(
                    "  Rewriting invalid selector %r → click_text(%r)",
                    selector,
                    text_match,
                )
                return await _dispatch_action(
                    {"action": "click_text", "text": text_match},
                    page,
                    input_domain,
                    runtime,
                )
            success = await input_domain.click(selector)
            if success:
                return f"Clicked element: {selector}"
            # Fallback: try JS click with shadow DOM pierce
            safe_sel = json.dumps(selector)
            js_result = await runtime.evaluate(
                f"(() => {{ {_PIERCE_JS} "
                f"const el = pierce(document, {safe_sel}); "
                f"if(el) {{ el.click(); return true; }} return false; }})()"
            )
            if js_result:
                return f"Clicked element via JS fallback: {selector}"
            return f"[Failed] Element not found: {selector}"

        elif action_type == "click_text":
            # Fallback action: click by visible text
            text = action["text"]
            safe_text = json.dumps(text)
            js = (
                f"(() => {{ "
                f"const els = [...document.querySelectorAll('a, button, [role=button], input[type=submit]')]; "
                f"const el = els.find(e => e.innerText?.trim().includes({safe_text})); "
                f"if(el) {{ el.click(); return true; }} return false; "
                f"}})()"
            )
            result = await runtime.evaluate(js)
            if result:
                return f"Clicked element by text: '{text}'"
            return f"[Failed] No element with text: '{text}'"

        elif action_type == "type":
            selector = action.get("selector")
            text = action["text"]
            await input_domain.type_text(text, selector=selector)
            return f"Typed '{text}' into {selector or 'focused element'}"

        elif action_type == "press_key":
            key = action["key"]
            await input_domain.press_key(key)
            return f"Pressed key: {key}"

        elif action_type == "navigate":
            url = action["url"]
            await page.navigate(url)
            return f"Navigated to: {url}"

        elif action_type == "scroll":
            direction = action.get("direction", "down")
            delta_y = -400 if direction == "down" else 400
            await input_domain.scroll(delta_y=delta_y)
            return f"Scrolled {direction}"

        elif action_type == "scroll_to":
            selector = action["selector"]
            safe_sel = json.dumps(selector)
            await runtime.evaluate(
                f"(() => {{ {_PIERCE_JS} "
                f"pierce(document, {safe_sel})?.scrollIntoView({{behavior: 'smooth', block: 'center'}}); }})()"
            )
            await asyncio.sleep(0.1)  # tiny delay for UI to paint
            return f"Scrolled to element: {selector}"

        elif action_type == "wait":
            selector = action["selector"]
            found = await page.wait_for_selector(selector)
            return f"{'Found' if found else 'Timeout waiting for'}: {selector}"

        elif action_type == "observe":
            return "Observing current page state"

        else:
            logger.warning(f"Unknown action type: {action_type}")
            return f"[Unknown action: {action_type}]"

    except websockets.exceptions.ConnectionClosed as e:
        logger.error("CDP Connection closed mid-action (!)")
        raise e
    except Exception as e:
        logger.error(f"Action execution error: {e}")
        return f"[Error] {action_type}: {e}"
