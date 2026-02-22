"""
Executor Agent — takes a step and page context, produces and executes a CDP action.
"""

import json
import logging

from ..cdp.input import InputDomain
from ..cdp.page import PageDomain
from ..cdp.runtime import RuntimeDomain
from ..llm.client import LLMClient
from ..llm.prompts import EXECUTOR_SYSTEM

logger = logging.getLogger(__name__)


async def execute_step(
    step: str,
    dom_html: str,
    interactive_elements: str,
    llm: LLMClient,
    page: PageDomain,
    input_domain: InputDomain,
    runtime: RuntimeDomain,
) -> str:
    """
    Execute a single documentation step via LLM-guided CDP actions.

    Args:
        step: The step description to execute
        dom_html: Current page DOM HTML
        interactive_elements: JSON list of interactive elements
        llm: LLM client
        page: CDP Page domain
        input_domain: CDP Input domain
        runtime: CDP Runtime domain

    Returns:
        Description of the action taken
    """
    user_prompt = (
        f"STEP TO EXECUTE: {step}\n\n"
        f"INTERACTIVE ELEMENTS:\n{interactive_elements}\n\n"
        f"PAGE HTML (truncated):\n{dom_html[:3000]}\n\n"
        f"Output the JSON action to execute this step:"
    )

    messages = [
        {"role": "system", "content": EXECUTOR_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]

    response = await llm.complete(messages, json_mode=True)
    action = _parse_action(response)

    if action is None:
        logger.warning(f"Could not parse action for step: {step}")
        return f"[Skipped] Could not determine action for: {step}"

    return await _dispatch_action(action, page, input_domain, runtime)


def _parse_action(response: str) -> dict | None:
    """Parse JSON action from LLM response."""
    try:
        # Try direct parse
        return json.loads(response.strip())
    except json.JSONDecodeError:
        pass

    # Try to extract JSON from markdown code block
    import re
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
            success = await input_domain.click(selector)
            import asyncio
            await asyncio.sleep(1.0)  # Wait for UI reaction
            if success:
                return f"Clicked element: {selector}"
            return f"[Failed] Element not found: {selector}"

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

        elif action_type == "wait":
            selector = action["selector"]
            found = await page.wait_for_selector(selector)
            return f"{'Found' if found else 'Timeout waiting for'}: {selector}"

        elif action_type == "observe":
            return "Observing current page state"

        else:
            logger.warning(f"Unknown action type: {action_type}")
            return f"[Unknown action: {action_type}]"

    except Exception as e:
        logger.error(f"Action execution error: {e}")
        return f"[Error] {action_type}: {e}"
