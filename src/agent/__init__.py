"""Public agent conveniences without eagerly loading optional LLM dependencies."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .loop import AgenticLoop

__all__ = ["AgenticLoop"]


def __getattr__(name: str):
    if name == "AgenticLoop":
        from .loop import AgenticLoop

        return AgenticLoop
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
