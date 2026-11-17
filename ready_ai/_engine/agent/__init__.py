"""Public agent conveniences, imported lazily on attribute access.

``AgenticLoop`` is resolved on first use (not at package import) to
keep ``import ready_ai._engine.agent`` cheap. The loop imports the LLM
client, but LiteLLM itself is optional until a command actually calls
the model (``pip install 'ready-ai[llm]'``).
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .loop import AgenticLoop

__all__ = ["AgenticLoop"]


def __getattr__(name: str):
    if name == "AgenticLoop":
        from .loop import AgenticLoop

        return AgenticLoop
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
