"""
Markdown Documentation Renderer.

Accumulates steps with screenshots and annotations into a structured
markdown document.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Language labels ─────────────────────────────────────────────────────────

_LANG_ALIASES: dict[str, str] = {
    "en": "english",
    "pt": "portuguese",
    "es": "spanish",
    "fr": "french",
    "de": "german",
    "it": "italian",
}

_LABELS: dict[str, dict[str, str]] = {
    "english": {
        "index":                   "Index",
        "step":                    "Step",
        "generated_at":            "Automatically generated documentation on",
        "technical_details":       "Technical details",
        "action_executed":         "Action executed:",
        "review_notes":            "Review Notes",
        "review":                  "Review",
        "improvement_suggestions": "Improvement suggestions:",
        "step_skipped":            "Step skipped",
        "manual_required":         "Manual action required",
    },
    "portuguese": {
        "index":                   "Índice",
        "step":                    "Passo",
        "generated_at":            "Documentação gerada automaticamente em",
        "technical_details":       "Detalhes técnicos",
        "action_executed":         "Ação executada:",
        "review_notes":            "Notas de Revisão",
        "review":                  "Revisão",
        "improvement_suggestions": "Sugestões de melhoria:",
        "step_skipped":            "Passo ignorado",
        "manual_required":         "Ação manual necessária",
    },
    "spanish": {
        "index":                   "Índice",
        "step":                    "Paso",
        "generated_at":            "Documentación generada automáticamente el",
        "technical_details":       "Detalles técnicos",
        "action_executed":         "Acción ejecutada:",
        "review_notes":            "Notas de revisión",
        "review":                  "Revisión",
        "improvement_suggestions": "Sugerencias de mejora:",
        "step_skipped":            "Paso omitido",
        "manual_required":         "Acción manual requerida",
    },
    "french": {
        "index":                   "Sommaire",
        "step":                    "Étape",
        "generated_at":            "Documentation générée automatiquement le",
        "technical_details":       "Détails techniques",
        "action_executed":         "Action exécutée :",
        "review_notes":            "Notes de révision",
        "review":                  "Révision",
        "improvement_suggestions": "Suggestions d'amélioration :",
        "step_skipped":            "Étape ignorée",
        "manual_required":         "Action manuelle requise",
    },
    "german": {
        "index":                   "Inhaltsverzeichnis",
        "step":                    "Schritt",
        "generated_at":            "Automatisch generierte Dokumentation vom",
        "technical_details":       "Technische Details",
        "action_executed":         "Ausgeführte Aktion:",
        "review_notes":            "Überprüfungsnotizen",
        "review":                  "Überprüfung",
        "improvement_suggestions": "Verbesserungsvorschläge:",
        "step_skipped":            "Schritt übersprungen",
        "manual_required":         "Manuelle Aktion erforderlich",
    },
    "italian": {
        "index":                   "Indice",
        "step":                    "Passo",
        "generated_at":            "Documentazione generata automaticamente il",
        "technical_details":       "Dettagli tecnici",
        "action_executed":         "Azione eseguita:",
        "review_notes":            "Note di revisione",
        "review":                  "Revisione",
        "improvement_suggestions": "Suggerimenti per il miglioramento:",
        "step_skipped":            "Passaggio saltato",
        "manual_required":         "Azione manuale richiesta",
    },
}


def _resolve_labels(language: Optional[str]) -> dict[str, str]:
    """Return the label dict for the given language, falling back to English."""
    if not language:
        return _LABELS["english"]
    key = language.strip().lower()
    key = _LANG_ALIASES.get(key, key)
    return _LABELS.get(key, _LABELS["english"])


# ─── Data model ──────────────────────────────────────────────────────────────


@dataclass
class DocStep:
    """A single documentation step."""
    step_number: int
    title: str
    screenshot_b64: str
    annotation: str
    action_description: str
    status: str = "completed"
    status_reason: str = ""


# ─── Renderer ────────────────────────────────────────────────────────────────


class DocRenderer:
    """Builds markdown documentation from accumulated steps."""

    def __init__(
        self,
        goal: str,
        title: Optional[str] = None,
        language: Optional[str] = None,
    ):
        self.goal = goal
        self.title = title
        self._labels = _resolve_labels(language)
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
        status: str = "completed",
        status_reason: str = "",
    ) -> None:
        """Add a documentation step with its screenshot and annotation."""
        step = DocStep(
            step_number=step_number,
            title=title,
            screenshot_b64=screenshot_b64,
            annotation=annotation,
            action_description=action_description,
            status=status,
            status_reason=status_reason,
        )
        self.steps.append(step)

        # Store screenshot for later file output
        if screenshot_b64:
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
        lb = self._labels
        lines = []

        # Header
        lines.append(f"# {self.title or self.goal}")
        lines.append("")
        lines.append(
            f"> {lb['generated_at']} "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        lines.append("")
        lines.append("---")
        lines.append("")

        # Table of contents
        if len(self.steps) > 3:
            lines.append(f"## {lb['index']}")
            lines.append("")
            for step in self.steps:
                anchor = f"{lb['step'].lower()}-{step.step_number}"
                lines.append(
                    f"{step.step_number}. [{step.title}](#{anchor})"
                )
            lines.append("")
            lines.append("---")
            lines.append("")

        # Steps
        for step in self.steps:
            lines.append(f"## {lb['step']} {step.step_number}: {step.title}")
            lines.append("")

            # Screenshot reference (file-based, not inline base64)
            screenshot_file = f"screenshots/step_{step.step_number:02d}.png"
            lines.append(f"![{lb['step']} {step.step_number}]({screenshot_file})")
            lines.append("")

            # Annotation
            lines.append(step.annotation)
            lines.append("")

            if step.status == "manual_required":
                lines.append(f"> **{lb['manual_required']}:** {step.status_reason}")
                lines.append("")
            elif step.status == "skipped":
                lines.append(f"> **{lb['step_skipped']}:** {step.status_reason}")
                lines.append("")

            # Action detail (collapsible)
            if step.action_description:
                lines.append("<details>")
                lines.append(f"<summary>{lb['technical_details']}</summary>")
                lines.append("")
                lines.append(f"**{lb['action_executed']}** {step.action_description}")
                lines.append("")
                lines.append("</details>")
                lines.append("")

            lines.append("---")
            lines.append("")

        # Critic notes (if any)
        if self._critic_notes:
            lines.append(f"## {lb['review_notes']}")
            lines.append("")
            for i, (feedback, suggestions) in enumerate(self._critic_notes, 1):
                lines.append(f"### {lb['review']} {i}")
                lines.append("")
                lines.append(feedback)
                lines.append("")
                if suggestions:
                    lines.append(f"**{lb['improvement_suggestions']}**")
                    for s in suggestions:
                        lines.append(f"- {s}")
                    lines.append("")

        return "\n".join(lines)
