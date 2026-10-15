"""Public agent conveniences, imported lazily on attribute access.

``AgenticLoop`` is resolved on first use (not at package import) to
keep ``import src.agent`` cheap — but the loop itself pulls in
``litellm`` via ``planner``/``llm.client``, which is a required
runtime dependency, not an optional one.
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
