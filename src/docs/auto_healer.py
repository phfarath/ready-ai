"""
Auto-Healer — automatically updates documentation when UI drift is detected.

When a step shows DRIFT (visual change) but still executes successfully,
the healer can:
1. Replace the baseline screenshot with the current one
2. Regenerate the annotation via LLM vision
3. Update the docs.md file in-place
4. Recover broken selectors via LLM

This transforms the system from "detection" to "auto-correction".
"""

import base64
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent.test_runner import StepTestResult, DocTestReport
    from ..llm.client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class HealResult:
    """Result of healing a single step."""
    step_number: int
    screenshot_updated: bool = False
    annotation_updated: bool = False
    selector_recovered: bool = False
    new_annotation: str = ""
    new_selector: str = ""
    error: str = ""


@dataclass
class HealingReport:
    """Summary of all healing actions taken."""
    steps_healed: list[HealResult] = field(default_factory=list)
    doc_rewritten: bool = False

    @property
    def total_healed(self) -> int:
        return sum(
            1 for r in self.steps_healed
            if r.screenshot_updated or r.annotation_updated or r.selector_recovered
        )


_ANNOTATOR_PROMPT = """You are a technical writer creating user documentation. Given a screenshot of a SaaS application and the step being documented, write a clear, concise annotation.

The annotation should:
- Describe what the user sees on screen
- Highlight the key UI element for this step
- Provide any helpful tips or context
- Be written as if guiding a new user through the interface
- Be 2-4 sentences maximum

Step: {step_title}

Write the annotation now:"""


_SELECTOR_RECOVERY_PROMPT = """You are a browser automation expert. A CSS selector used in documentation no longer matches any element on the page.

Original action description: {original_action}
Original selector (broken): {original_selector}

Current interactive elements on the page:
{current_elements}

Find the element that best matches the original intent and return ONLY a JSON object:
{{"found": true, "selector": "new_css_selector", "reason": "brief explanation"}}

If no equivalent element exists:
{{"found": false, "selector": "", "reason": "why the element is gone"}}"""


class DocAutoHealer:
    """Automatically heals drifted documentation."""

    def __init__(self, doc_path: str, llm: "LLMClient"):
        self.doc_path = Path(doc_path)
        self.doc_dir = self.doc_path.parent
        self.llm = llm
        self._doc_content = self.doc_path.read_text(encoding="utf-8")

    async def heal_report(
        self,
        report: "DocTestReport",
    ) -> HealingReport:
        """Heal all drifted steps in a test report."""
        healing = HealingReport()

        for result in report.results:
            if result.status == "DRIFT" and result.new_screenshot_path:
                heal_result = await self.heal_step(result)
                healing.steps_healed.append(heal_result)

        # Rewrite docs.md if any changes were made
        if healing.total_healed > 0:
            self.doc_path.write_text(self._doc_content, encoding="utf-8")
            healing.doc_rewritten = True
            logger.info(f"Auto-healed {healing.total_healed} steps in {self.doc_path}")

        return healing

    async def heal_step(self, result: "StepTestResult") -> HealResult:
        """Heal a single drifted step: update screenshot and annotation."""
        heal = HealResult(step_number=result.step_number)

        try:
            # 1. Replace baseline screenshot
            if result.new_screenshot_path:
                baseline_path = self.doc_dir / f"screenshots/step_{result.step_number:02d}.png"
                baseline_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(result.new_screenshot_path, baseline_path)
                heal.screenshot_updated = True
                logger.info(f"  Step {result.step_number}: screenshot updated")

            # 2. Regenerate annotation via LLM vision
            if result.new_screenshot_path:
                new_annotation = await self._regenerate_annotation(
                    result.step_number, result.title, result.new_screenshot_path
                )
                if new_annotation:
                    self._update_annotation_in_doc(result.step_number, new_annotation)
                    heal.annotation_updated = True
                    heal.new_annotation = new_annotation
                    logger.info(f"  Step {result.step_number}: annotation updated")

        except Exception as e:
            heal.error = str(e)
            logger.error(f"  Step {result.step_number}: heal failed — {e}")

        return heal

    async def recover_selector(
        self,
        step_number: int,
        original_action: str,
        current_elements: str,
    ) -> HealResult:
        """Try to find an equivalent selector when the original one broke."""
        heal = HealResult(step_number=step_number)

        # Extract selector from action description (e.g., "Clicked element: button#login-btn")
        selector_match = re.search(r"(?:element|selector):\s*(.+?)$", original_action, re.IGNORECASE)
        original_selector = selector_match.group(1).strip() if selector_match else original_action

        try:
            prompt = _SELECTOR_RECOVERY_PROMPT.format(
                original_action=original_action,
                original_selector=original_selector,
                current_elements=current_elements[:3000],
            )

            response = await self.llm.complete(
                [{"role": "user", "content": prompt}],
                json_mode=True,
                role="healer",
            )

            import json
            data = json.loads(response)
            if data.get("found"):
                new_selector = data["selector"]
                self._update_action_in_doc(step_number, original_action, new_selector)
                heal.selector_recovered = True
                heal.new_selector = new_selector
                logger.info(
                    f"  Step {step_number}: selector recovered "
                    f"'{original_selector}' → '{new_selector}'"
                )
            else:
                logger.warning(
                    f"  Step {step_number}: selector recovery failed — {data.get('reason', 'unknown')}"
                )

        except Exception as e:
            heal.error = str(e)
            logger.error(f"  Step {step_number}: selector recovery error — {e}")

        return heal

    async def _regenerate_annotation(
        self, step_number: int, title: str, screenshot_path: str,
    ) -> str:
        """Use LLM vision to generate a new annotation from the current screenshot."""
        img_data = Path(screenshot_path).read_bytes()
        img_b64 = base64.b64encode(img_data).decode()

        prompt = _ANNOTATOR_PROMPT.format(step_title=title)
        return await self.llm.complete_with_vision(
            prompt=prompt,
            image_b64=img_b64,
            role="healer",
        )

    def _update_annotation_in_doc(self, step_number: int, new_annotation: str) -> None:
        """Replace the annotation text for a step in the doc content."""
        # Pattern: find the screenshot image, then the text before <details>
        pattern = re.compile(
            r"(!\[.*?\]\(screenshots/step_"
            + f"{step_number:02d}"
            + r"\.png\)\s*\n\s*\n)"  # screenshot line + blank line
            + r"(.*?)"               # annotation (capture group)
            + r"(\n\s*\n<details>)",  # blank line + details block
            re.DOTALL,
        )

        def replacer(m):
            return m.group(1) + new_annotation + m.group(3)

        new_content = pattern.sub(replacer, self._doc_content, count=1)
        if new_content != self._doc_content:
            self._doc_content = new_content

    def _update_action_in_doc(
        self, step_number: int, original_action: str, new_selector: str,
    ) -> None:
        """Replace the action selector in the doc content for a step."""
        # Replace selector in the action description
        if original_action in self._doc_content:
            # Build new action description with recovered selector
            selector_re = re.search(r"(element|selector):\s*.+?$", original_action, re.IGNORECASE)
            if selector_re:
                old_part = selector_re.group(0)
                new_part = f"{selector_re.group(1)}: {new_selector}"
                new_action = original_action.replace(old_part, new_part)
                self._doc_content = self._doc_content.replace(original_action, new_action, 1)
