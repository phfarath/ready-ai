"""
Planner Agent — takes a goal and page context, produces a step-by-step plan.
"""

import logging
import re

from ..llm.client import LLMClient
from ..llm.prompts import PLANNER_SYSTEM

logger = logging.getLogger(__name__)


async def plan(
    goal: str,
    dom_html: str,
    interactive_elements: str,
    llm: LLMClient,
    language: str | None = None,
) -> list[str]:
    """
    Generate a numbered plan of UI steps to accomplish the documentation goal.

    Args:
        goal: What to document (e.g., "Document the login flow")
        dom_html: Current page DOM HTML (truncated for context)
        interactive_elements: JSON list of interactive elements on page
        llm: LLM client instance
        language: Output language override (e.g. "English", "Portuguese")

    Returns:
        List of step strings (without numbering prefix)
    """
    user_prompt = (
        f"GOAL: {goal}\n\n"
        f"INTERACTIVE ELEMENTS ON PAGE:\n{interactive_elements}\n\n"
        f"PAGE HTML (truncated):\n{dom_html[:3000]}\n\n"
        f"Create the step-by-step plan:"
    )
    if language:
        user_prompt += f"\nIMPORTANT: Write all output in {language}."

    messages = [
        {"role": "system", "content": PLANNER_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]

    response = await llm.complete(messages)
    steps = _parse_steps(response)

    logger.info(f"Planner generated {len(steps)} steps")
    for i, step in enumerate(steps, 1):
        logger.debug(f"  Step {i}: {step}")

    return steps


def _parse_steps(response: str) -> list[str]:
    """
    Parse a numbered step list from LLM response.

    Handles formats like:
        1. Step one
        2. Step two
        1) Step one
        - Step one
    """
    lines = response.strip().split("\n")
    steps = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Remove numbering: "1. ", "1) ", "- ", "* "
        cleaned = re.sub(r"^\d+[\.\)]\s*", "", line)
        cleaned = re.sub(r"^[-\*]\s*", "", cleaned)
        cleaned = cleaned.strip()

        if cleaned:
            steps.append(cleaned)

    return steps
