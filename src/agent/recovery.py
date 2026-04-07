"""
Recovery — failed step recovery, SPA drift detection, and replanning.

Module-level async functions following the same pattern as planner.py and critic.py.
All functions receive explicit dependencies as parameters for testability.
"""

import hashlib
import json
import logging
import re
from typing import Optional

from ..cdp.page import PageDomain
from ..cdp.input import InputDomain
from ..cdp.runtime import RuntimeDomain
from ..llm.client import LLMClient
from ..llm.prompts import (
    PLANNER_FAILED_STEP_RECOVERY_SYSTEM,
    PLANNER_REPLAN_SYSTEM,
    PLANNER_SPA_REPLAN_SYSTEM,
)
from ..observability import get_metrics, log_event
from . import planner, executor

logger = logging.getLogger(__name__)


async def dom_fingerprint(runtime: RuntimeDomain) -> str:
    """Compute a fast hash for SPA-relevant interactive DOM state."""
    js = """
        (() => {
            const selectors = [
                'button', 'input', 'select', 'textarea',
                '[role="tab"]', '[role="menuitem"]',
                '[aria-expanded]', '[aria-selected]', '[data-state]'
            ].join(',');

            const normalize = (value) => (value || '')
                .replace(/\\s+/g, ' ')
                .trim()
                .slice(0, 50);

            const entries = Array.from(document.querySelectorAll(selectors))
                .filter(el => {
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                })
                .map(el => {
                    const text = normalize(el.innerText || el.textContent || el.value);
                    const state = [
                        el.tagName.toLowerCase(),
                        el.getAttribute('role') || '',
                        text,
                        el.getAttribute('aria-expanded') || '',
                        el.getAttribute('aria-selected') || '',
                        el.getAttribute('data-state') || '',
                        el.hasAttribute('disabled') ? 'disabled' : 'enabled',
                    ];
                    return state.join('|');
                });

            const uniqueSorted = Array.from(new Set(entries)).sort();
            return uniqueSorted.slice(0, 250).join('\\n');
        })()
    """
    try:
        payload = await runtime.evaluate(js)
        payload_str = str(payload) if payload is not None else ""
    except Exception:
        payload_str = ""
    return hashlib.md5(payload_str.encode("utf-8")).hexdigest()


def is_spa_drift(
    pre_fingerprint: str,
    post_fingerprint: str,
    result_success: bool,
    pre_url: str,
    post_url: str,
) -> bool:
    """SPA drift policy (v1): failed step + changed fingerprint + same URL."""
    return (
        result_success is False
        and pre_fingerprint != post_fingerprint
        and pre_url == post_url
    )


def parse_recovery_decision(response: str) -> dict:
    """Parse JSON returned by local failed-step recovery prompt."""
    try:
        return json.loads(response.strip())
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
        if match:
            return json.loads(match.group(1))
    return {"decision": "mark_manual", "reason": "Recovery decision could not be parsed."}


async def recover_failed_step(
    step: str,
    result: executor.StepResult,
    pre_url: str,
    post_url: str,
    pre_fingerprint: str,
    post_fingerprint: str,
    page: PageDomain,
    input_domain: InputDomain,
    runtime: RuntimeDomain,
    llm: LLMClient,
    replan_attempts: int,
    max_replans_per_step: int = 2,
    language: Optional[str] = None,
) -> tuple[executor.StepResult, str, int]:
    """Try bounded local recovery before finalizing a failed step."""
    working_step = step
    working_result = result

    if (
        replan_attempts < max_replans_per_step
        and is_spa_drift(
            pre_fingerprint=pre_fingerprint,
            post_fingerprint=post_fingerprint,
            result_success=working_result.success,
            pre_url=pre_url,
            post_url=post_url,
        )
    ):
        logger.warning(
            "    ⚠ SPA state drift detected (same URL, fingerprint changed, step failed)"
        )
        metrics = get_metrics()
        if metrics:
            metrics.increment("recovery.spa_drift")
        log_event("recovery_spa_drift", step=working_step, url=post_url)
        latest_dom_html = await page.get_dom_html(max_length=4000)
        latest_elements = await runtime.get_interactive_elements()
        adapted_step = await replan_spa_step(
            failed_step=working_step,
            failure_reason=working_result.failure_reason,
            action_desc=working_result.action_desc,
            current_url=post_url,
            dom_html=latest_dom_html,
            elements=latest_elements,
            llm=llm,
            language=language,
        )
        if adapted_step:
            logger.info("    ⟳ SPA-replanned current step: '%s' → '%s'", working_step, adapted_step)
            replan_attempts += 1
            working_step = adapted_step
            working_result = await executor.execute_step(
                working_step,
                latest_dom_html,
                latest_elements,
                llm,
                page,
                input_domain,
                runtime,
                current_url=post_url,
            )
            if working_result.success:
                return working_result, working_step, replan_attempts

    if replan_attempts >= max_replans_per_step:
        logger.warning("    ⚠ Replan limit reached for current step; marking final outcome")
        return working_result, working_step, replan_attempts

    latest_dom_html = await page.get_dom_html(max_length=4000)
    latest_elements = await runtime.get_interactive_elements()
    decision = await recover_locally(
        failed_step=working_step,
        result=working_result,
        current_url=post_url,
        dom_html=latest_dom_html,
        elements=latest_elements,
        llm=llm,
        language=language,
    )

    if decision.get("decision") == "retry_with_adapted_step" and decision.get("step"):
        adapted_step = decision["step"].strip()
        logger.info("    ⟳ Locally adapted failed step: '%s' → '%s'", working_step, adapted_step)
        metrics = get_metrics()
        if metrics:
            metrics.increment("recovery.local_recovery")
        replan_attempts += 1
        retry_result = await executor.execute_step(
            adapted_step,
            latest_dom_html,
            latest_elements,
            llm,
            page,
            input_domain,
            runtime,
            current_url=post_url,
        )
        return retry_result, adapted_step, replan_attempts

    if decision.get("decision") == "skip_step":
        return (
            executor.StepResult(
                action_desc=working_result.action_desc,
                success=False,
                retry_needed=False,
                attempts=working_result.attempts,
                failure_reason=decision.get("reason") or working_result.failure_reason,
                status="skipped",
            ),
            working_step,
            replan_attempts,
        )

    return (
        executor.StepResult(
            action_desc=working_result.action_desc,
            success=False,
            retry_needed=False,
            attempts=working_result.attempts,
            failure_reason=decision.get("reason") or working_result.failure_reason,
            status="manual_required",
        ),
        working_step,
        replan_attempts,
    )


async def recover_locally(
    failed_step: str,
    result: executor.StepResult,
    current_url: str,
    dom_html: str,
    elements: str,
    llm: LLMClient,
    language: Optional[str] = None,
) -> dict:
    """Ask the LLM for a bounded recovery decision for a failed step."""
    user_prompt = (
        f"CURRENT PAGE URL: {current_url}\n\n"
        f"FAILED CURRENT STEP:\n- {failed_step}\n\n"
        f"LAST ACTION DESCRIPTION:\n{result.action_desc}\n\n"
        f"FAILURE REASON:\n{result.failure_reason or 'No explicit reason provided'}\n\n"
        f"INTERACTIVE ELEMENTS ON CURRENT PAGE:\n{elements}\n\n"
        f"PAGE HTML (truncated):\n{dom_html[:3000]}\n\n"
        "Decide whether to adapt the step, skip it, or mark it as manual."
    )
    if language:
        user_prompt += f"\nIMPORTANT: Write all output in {language}."

    messages = [
        {"role": "system", "content": PLANNER_FAILED_STEP_RECOVERY_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = await llm.complete(messages, json_mode=True, role="recovery")
        return parse_recovery_decision(response)
    except Exception as exc:
        logger.warning("    Local failed-step recovery fell back to manual handling: %s", exc)
        return {"decision": "mark_manual", "reason": result.failure_reason}


async def replan_remaining(
    remaining_steps: list[str],
    dom_html: str,
    elements: str,
    current_url: str,
    llm: LLMClient,
    language: Optional[str] = None,
) -> list[str]:
    """
    Replan remaining steps after an unexpected URL change.
    Returns adapted step list, or the original list if replanning fails.
    """
    remaining_context = "\n".join(f"- {s}" for s in remaining_steps)
    user_prompt = (
        f"CURRENT PAGE URL: {current_url}\n\n"
        f"REMAINING PLANNED STEPS (may reference elements from the previous page):\n"
        f"{remaining_context}\n\n"
        f"INTERACTIVE ELEMENTS ON CURRENT PAGE:\n{elements}\n\n"
        f"PAGE HTML (truncated):\n{dom_html[:3000]}\n\n"
        f"The browser navigated to a new page. Adapt the remaining steps to this "
        f"page's context. Preserve steps that still apply, modify steps that "
        f"reference old-page elements, and remove steps that are no longer relevant. "
        f"Output ONLY the numbered list:"
    )
    if language:
        user_prompt += f"\nIMPORTANT: Write all output in {language}."

    messages = [
        {"role": "system", "content": PLANNER_REPLAN_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = await llm.complete(messages, role="recovery")
        new_steps = planner._parse_steps(response)
        return new_steps if new_steps else remaining_steps
    except Exception as e:
        logger.warning(f"    Replanning failed: {e}, continuing with original steps")
        return remaining_steps


async def replan_spa_step(
    failed_step: str,
    failure_reason: str,
    action_desc: str,
    current_url: str,
    dom_html: str,
    elements: str,
    llm: LLMClient,
    language: Optional[str] = None,
) -> Optional[str]:
    """
    Adapt only the current failed step for SPA state changes on the same URL.
    Returns an adapted single step, or None if replanning fails.
    """
    user_prompt = (
        f"CURRENT PAGE URL: {current_url}\n\n"
        f"FAILED CURRENT STEP:\n- {failed_step}\n\n"
        f"LAST ACTION DESCRIPTION:\n{action_desc}\n\n"
        f"FAILURE REASON:\n{failure_reason or 'No explicit reason provided'}\n\n"
        f"INTERACTIVE ELEMENTS ON CURRENT PAGE:\n{elements}\n\n"
        f"PAGE HTML (truncated):\n{dom_html[:3000]}\n\n"
        f"The URL stayed the same, but SPA state changed. Rewrite ONLY the failed "
        f"current step so it is executable in this updated in-page state. "
        f"Output ONLY one numbered step."
    )
    if language:
        user_prompt += f"\nIMPORTANT: Write all output in {language}."

    messages = [
        {"role": "system", "content": PLANNER_SPA_REPLAN_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = await llm.complete(messages, role="recovery")
        candidate_steps = planner._parse_steps(response)
        if not candidate_steps:
            return None
        return candidate_steps[0]
    except Exception as e:
        logger.warning(f"    SPA step replanning failed: {e}")
        return None
