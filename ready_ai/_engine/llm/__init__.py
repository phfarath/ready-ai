"""Prompt exports plus a lazily-resolved LLM client.

``LLMClient`` is resolved on first attribute access to keep
``import ready_ai._engine.llm`` cheap. Constructing a client needs the
optional ``llm`` extra (``pip install 'ready-ai[llm]'``); importing this
package does not.
"""

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
