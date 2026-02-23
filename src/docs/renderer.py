"""
Markdown Documentation Renderer.

Accumulates steps with screenshots and annotations into a structured
markdown document.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DocStep:
    """A single documentation step."""
    step_number: int
    title: str
    screenshot_b64: str
    annotation: str
    action_description: str


class DocRenderer:
    """Builds markdown documentation from accumulated steps."""

    def __init__(self, goal: str, title: Optional[str] = None):
        self.goal = goal
        self.title = title
        self.steps: list[DocStep] = []
        self.screenshots: dict[str, str] = {}  # filename → base64 data
        self._critic_notes: list[tuple[str, list[str]]] = []

    def add_step(
        self,
        step_number: int,
        title: str,
        screenshot_b64: str,
        annotation: str,
        action_description: str = "",
    ) -> None:
        """Add a documentation step with its screenshot and annotation."""
        step = DocStep(
            step_number=step_number,
            title=title,
            screenshot_b64=screenshot_b64,
            annotation=annotation,
            action_description=action_description,
        )
        self.steps.append(step)

        # Store screenshot for later file output
        filename = f"step_{step_number:02d}.png"
        self.screenshots[filename] = screenshot_b64

        logger.debug(f"Added step {step_number}: {title}")

    def add_critic_notes(self, feedback: str, suggestions: list[str]) -> None:
        """Add critic review notes to be appended to the document."""
        self._critic_notes.append((feedback, suggestions))

    def render(self) -> str:
        """
        Render the accumulated steps into a complete markdown document.

        Returns:
            Formatted markdown string
        """
        lines = []

        # Header
        lines.append(f"# {self.title or self.goal}")
        lines.append("")
        lines.append(
            f"> Documentação gerada automaticamente em "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        lines.append("")
        lines.append("---")
        lines.append("")

        # Table of contents
        if len(self.steps) > 3:
            lines.append("## Índice")
            lines.append("")
            for step in self.steps:
                anchor = f"passo-{step.step_number}"
                lines.append(
                    f"{step.step_number}. [{step.title}](#{anchor})"
                )
            lines.append("")
            lines.append("---")
            lines.append("")

        # Steps
        for step in self.steps:
            lines.append(f"## Passo {step.step_number}: {step.title}")
            lines.append("")

            # Screenshot reference (file-based, not inline base64)
            screenshot_file = f"screenshots/step_{step.step_number:02d}.png"
            lines.append(f"![Passo {step.step_number}]({screenshot_file})")
            lines.append("")

            # Annotation
            lines.append(step.annotation)
            lines.append("")

            # Action detail (collapsible)
            if step.action_description:
                lines.append("<details>")
                lines.append("<summary>Detalhes técnicos</summary>")
                lines.append("")
                lines.append(f"**Ação executada:** {step.action_description}")
                lines.append("")
                lines.append("</details>")
                lines.append("")

            lines.append("---")
            lines.append("")

        # Critic notes (if any)
        if self._critic_notes:
            lines.append("## Notas de Revisão")
            lines.append("")
            for i, (feedback, suggestions) in enumerate(self._critic_notes, 1):
                lines.append(f"### Revisão {i}")
                lines.append("")
                lines.append(feedback)
                lines.append("")
                if suggestions:
                    lines.append("**Sugestões de melhoria:**")
                    for s in suggestions:
                        lines.append(f"- {s}")
                    lines.append("")

        return "\n".join(lines)
