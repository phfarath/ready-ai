"""
Planner Agent — takes a goal and page context, produces a step-by-step plan.
"""

from __future__ import annotations

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
    webmcp_context: str | None = None,
) -> list[str]:
    """
    Generate a numbered plan of UI steps to accomplish the documentation goal.

    Args:
        goal: What to document (e.g., "Document the login flow")
        dom_html: Current page DOM HTML (truncated for context)
        interactive_elements: JSON list of interactive elements on page
        llm: LLM client instance
        language: Output language override (e.g. "English", "Portuguese")
        webmcp_context: Optional pre-formatted WebMCP tools section
            (US8, via ``format_webmcp_context``). None/empty omits it.

    Returns:
        List of step strings (without numbering prefix)
    """
    user_prompt = (
        f"GOAL: {goal}\n\n"
        f"INTERACTIVE ELEMENTS ON PAGE:\n{interactive_elements}\n\n"
        f"PAGE HTML (truncated):\n{dom_html[:3000]}\n\n"
    )
    if webmcp_context:
        user_prompt += f"{webmcp_context}\n\n"
    user_prompt += "Create the step-by-step plan:"
    if language:
        user_prompt += f"\nIMPORTANT: Write all output in {language}."

    messages = [
        {"role": "system", "content": PLANNER_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]

    response = await llm.complete(messages, role="planner")
    steps = _parse_steps(response)

    logger.info(f"Planner generated {len(steps)} steps")
    for i, step in enumerate(steps, 1):
        logger.debug(f"  Step {i}: {step}")

    return steps


def format_webmcp_context(tools: list[dict]) -> str:
    """Format detected WebMCP tools as a planner prompt section (US8).

    Returns ``""`` when there are no tools so callers can omit the
    section entirely. Schemas are page-declared capabilities: names
    and short descriptions only, marked opportunistic — the planner
    must still verify against the live DOM before acting.
    """
    if not tools:
        return ""
    lines = [
        "WEBMCP TOOLS (page-declared, opportunistic — verify on the live page before acting):"
    ]
    for tool in tools:
        name = tool.get("name", "?")
        description = (tool.get("description") or "").strip()
        entry = f"- {name}"
        if description:
            entry += f": {description}"
        lines.append(entry)
    return "\n".join(lines)


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
