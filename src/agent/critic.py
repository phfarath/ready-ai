"""
Critic Agent — reviews generated documentation and provides quality feedback.
"""

import json
import logging
from dataclasses import dataclass

from ..llm.client import LLMClient
from ..llm.prompts import CRITIC_SYSTEM
from ..observability import get_metrics, log_event

logger = logging.getLogger(__name__)


@dataclass
class CriticFeedback:
    """Structured feedback from the critic."""
    is_complete: bool
    score: int
    feedback: str
    missing_steps: list[str]
    suggestions: list[str]


async def review(docs_markdown: str, goal: str, llm: LLMClient) -> CriticFeedback:
    """
    Review generated documentation for quality and completeness.

    Args:
        docs_markdown: The generated markdown documentation
        goal: Original documentation goal
        llm: LLM client

    Returns:
        CriticFeedback with scores and suggestions
    """
    user_prompt = (
        f"GOAL: {goal}\n\n"
        f"GENERATED DOCUMENTATION:\n{docs_markdown}\n\n"
        f"Review this documentation and provide your assessment:"
    )

    messages = [
        {"role": "system", "content": CRITIC_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]

    response = await llm.complete(messages, json_mode=True, role="critic")

    try:
        data = json.loads(response)
        feedback = CriticFeedback(
            is_complete=data.get("is_complete", False),
            score=data.get("score", 0),
            feedback=data.get("feedback", ""),
            missing_steps=data.get("missing_steps", []),
            suggestions=data.get("suggestions", []),
        )
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Could not parse critic response: {e}")
        feedback = CriticFeedback(
            is_complete=True,  # Don't block on parse failure
            score=5,
            feedback=response[:500],
            missing_steps=[],
            suggestions=[],
        )

    logger.info(
        f"Critic review: score={feedback.score}/10, "
        f"complete={feedback.is_complete}, "
        f"suggestions={len(feedback.suggestions)}"
    )

    metrics = get_metrics()
    if metrics:
        metrics.record("critic.score", feedback.score)
    log_event(
        "critic_review",
        score=feedback.score,
        is_complete=feedback.is_complete,
        missing_steps=len(feedback.missing_steps),
    )

    return feedback
