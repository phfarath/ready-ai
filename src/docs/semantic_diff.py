"""
Semantic Diff — uses LLM vision to describe UI changes in natural language.

Instead of just showing pixel differences, this module produces human-readable
descriptions like "The 'Save' button changed from blue to green".
"""

import base64
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm.client import LLMClient

logger = logging.getLogger(__name__)


_SEMANTIC_DIFF_PROMPT = """Compare these two screenshots of a UI step titled "{step_title}".

The FIRST image is the baseline (how the UI looked before).
The SECOND image is the current state (how the UI looks now).

Describe what changed in 1-2 concise sentences. Focus on:
- Visual changes (colors, layout, positioning)
- Text changes (labels, content)
- Element changes (added, removed, moved)

If the images look identical, say "No visible changes detected."

Describe the changes now:"""


async def describe_visual_change(
    baseline_path: str | Path,
    current_path: str | Path,
    step_title: str,
    llm: "LLMClient",
) -> str:
    """
    Use LLM vision to describe what changed between two screenshots.

    Args:
        baseline_path: Path to the baseline screenshot.
        current_path: Path to the current screenshot.
        step_title: Title of the step being compared.
        llm: LLM client with vision capabilities.

    Returns:
        Human-readable description of the visual changes.
    """
    baseline_b64 = base64.b64encode(Path(baseline_path).read_bytes()).decode()
    current_b64 = base64.b64encode(Path(current_path).read_bytes()).decode()

    prompt = _SEMANTIC_DIFF_PROMPT.format(step_title=step_title)

    try:
        description = await llm.complete_with_vision_multi(
            prompt=prompt,
            images_b64=[baseline_b64, current_b64],
            role="semantic_diff",
        )
        logger.info(f"Semantic diff: {description[:100]}")
        return description.strip()
    except Exception as e:
        logger.warning(f"Semantic diff failed: {e}")
        return ""
