"""Prompt exports without eagerly importing the optional LiteLLM client."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import LLMClient
from .prompts import (
    PLANNER_SYSTEM,
    PLANNER_SUPPLEMENT_SYSTEM,
    EXECUTOR_SYSTEM,
    EXECUTOR_RETRY_SYSTEM,
    CRITIC_SYSTEM,
    ANNOTATOR_PROMPT,
)

__all__ = [
    "LLMClient",
    "PLANNER_SYSTEM",
    "PLANNER_SUPPLEMENT_SYSTEM",
    "EXECUTOR_SYSTEM",
    "EXECUTOR_RETRY_SYSTEM",
    "CRITIC_SYSTEM",
    "ANNOTATOR_PROMPT",
]


def __getattr__(name: str):
    if name == "LLMClient":
        from .client import LLMClient

        return LLMClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
