"""
Documentation Parser — extracts executable steps from a ready-ai generated docs.md file.

Used by the test runner to re-execute documented steps and verify they still work.
"""

import logging
import re
from pathlib import Path
from typing import Optional

from ..agent.state import DocStepState

logger = logging.getLogger(__name__)

# Step header patterns for all supported languages.
# Matches: "## Step 3: Click the button" or "## Passo 3: Clicar no botão"
_STEP_HEADER_RE = re.compile(
    r"^##\s+"
    r"(?:Step|Passo|Paso|Étape|Schritt)\s+"  # language variants
    r"(\d+)"                                  # step number
    r":\s+"
    r"(.+)$",                                 # title
    re.MULTILINE,
)

# Screenshot path inside markdown image syntax: ![...](screenshots/step_01.png)
_SCREENSHOT_RE = re.compile(
    r"!\[.*?\]\((screenshots/step_\d+\.png)\)"
)

# Action description inside <details> blocks
_ACTION_RE = re.compile(
    r"<details>.*?"
    r"\*\*(?:Action executed|Ação executada|Acción ejecutada|Action exécutée|Ausgeführte Aktion|Azione eseguita)\s*:\*\*\s*"
    r"(.+?)\s*"
    r"</details>",
    re.DOTALL,
)


def parse_doc(doc_path: str | Path) -> list[DocStepState]:
    """
    Parse a ready-ai generated docs.md into a list of DocStepState objects.

    Args:
        doc_path: Path to the docs.md file.

    Returns:
        List of DocStepState objects representing each documented step.

    Raises:
        FileNotFoundError: If the doc file does not exist.
        ValueError: If no steps could be parsed from the document.
    """
    path = Path(doc_path)
    content = path.read_text(encoding="utf-8")

    # Find all step header positions
    headers = list(_STEP_HEADER_RE.finditer(content))
    if not headers:
        raise ValueError(f"No steps found in {doc_path}")

    steps: list[DocStepState] = []

    for idx, match in enumerate(headers):
        step_number = int(match.group(1))
        title = match.group(2).strip()

        # Extract the section text between this header and the next (or EOF)
        start = match.end()
        end = headers[idx + 1].start() if idx + 1 < len(headers) else len(content)
        section = content[start:end]

        # Extract screenshot path
        screenshot_match = _SCREENSHOT_RE.search(section)
        screenshot_path = screenshot_match.group(1) if screenshot_match else f"screenshots/step_{step_number:02d}.png"

        # Extract action description from <details> block
        action_match = _ACTION_RE.search(section)
        action_description = action_match.group(1).strip() if action_match else ""

        # Extract annotation: text between screenshot and <details> (or next ---)
        annotation = _extract_annotation(section, screenshot_match, action_match)

        steps.append(DocStepState(
            number=step_number,
            title=title,
            action_description=action_description,
            annotation=annotation,
            screenshot_path=screenshot_path,
        ))

    logger.info(f"Parsed {len(steps)} steps from {doc_path}")
    return steps


def _extract_annotation(
    section: str,
    screenshot_match: Optional[re.Match],
    action_match: Optional[re.Match],
) -> str:
    """Extract the annotation text from a step section."""
    # Annotation is after the screenshot image and before the <details> block
    start = screenshot_match.end() if screenshot_match else 0
    end = action_match.start() if action_match else section.find("---")
    if end == -1:
        end = len(section)

    text = section[start:end].strip()
    # Remove leading/trailing blank lines
    lines = [line for line in text.split("\n") if line.strip()]
    return "\n".join(lines)


def extract_goal(doc_path: str | Path) -> Optional[str]:
    """
    Extract the documentation goal/title from the first H1 header.

    Args:
        doc_path: Path to the docs.md file.

    Returns:
        The goal string, or None if no H1 header is found.
    """
    path = Path(doc_path)
    content = path.read_text(encoding="utf-8")
    match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    return match.group(1).strip() if match else None
