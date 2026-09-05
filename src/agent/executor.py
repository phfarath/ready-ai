"""
Executor Agent — takes a step and page context, produces and executes a CDP action.

V2: With post-action verification, retry loop, and fallback strategies.
Each step gets up to MAX_RETRIES attempts. After each action, the DOM is
compared before/after to detect if anything actually changed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import websockets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from ..cdp.input import InputDomain
from ..cdp.exceptions import WebSocketDisconnected, CircuitOpenError
from ..cdp.page import PageDomain
from ..cdp.runtime import RuntimeDomain
from ..cdp.sanitize import is_sensitive_field
from .state import Expectation, OutcomeEvidence
from ..llm.prompts import EXECUTOR_SYSTEM, EXECUTOR_RETRY_SYSTEM

if TYPE_CHECKING:
    from ..llm.client import LLMClient

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

# ─── Effect policy taxonomy (READY-AI-T-PH2A) ──────────────────────────
#
# Every executor action maps to one effect level:
#   read     — observes only (observe, wait). Cannot change app state.
#   navigate — read plus browser navigation/scrolling. Moves the session,
#              never mutates application data.
#   write    — interacts with the page (click, type, keys...). May trigger
#              application side effects; gated by step/flow policy ceilings
#              and, when declared, explicit confirmation.
# Unknown action types map to None and are denied under every policy
# (fail closed — mirrors _flow_action_ok's treatment of unknown wording).
EFFECT_READ_ACTIONS: frozenset[str] = frozenset({"observe", "wait", "wait_for_popup"})
EFFECT_NAVIGATE_ACTIONS: frozenset[str] = frozenset(
    {"navigate", "scroll", "scroll_to", "switch_tab", "close_tab"}
)
# Declared executor actions that interact with the page. Anything else
# carrying a plain action name is treated as write (guilty until proven
# innocent); bracketed/empty names are unknown and denied everywhere.
EFFECT_WRITE_ACTIONS: frozenset[str] = frozenset(
    {"click", "click_text", "type", "press_key"}
)
STEP_POLICIES: tuple[str, ...] = ("read", "navigate", "write")


def action_effect_level(action_type: str) -> str | None:
    """Return the effect level of an action type, or None when unknown."""
    if action_type in EFFECT_READ_ACTIONS:
        return "read"
    if action_type in EFFECT_NAVIGATE_ACTIONS:
        return "navigate"
    if action_type in EFFECT_WRITE_ACTIONS:
        return "write"
    if isinstance(action_type, str) and action_type and not action_type.startswith("["):
        return "write"
    return None


def action_allowed_under_policy(action_type: str, policy: str) -> bool:
    """Fail-closed ceiling check: action level must be at or below policy."""
    order = {name: rank for rank, name in enumerate(STEP_POLICIES)}
    if policy not in order:
        return False
    level = action_effect_level(action_type)
    if level is None:
        return False
    return order[level] <= order[policy]

async def _resolve_action_session(
    action: dict, page: PageDomain,
) -> tuple[Any | None, str | None]:
    """Resolve an explicit action ``target`` to a CDP session id.

    Returns (session_id, failure): exactly one is set. Unresolvable
    references fail closed with the known-targets context.
    """
    ref = action.get("target")
    if ref is None:
        return None, None
    try:
        return await page.resolve_target_session(ref), None
    except RuntimeError as exc:
        return None, str(exc)


# Actions that accept an explicit ``target`` (anything else with a target
# fails closed — never silently runs on the primary tab).
_TARGET_SCOPED_ACTIONS: frozenset[str] = frozenset(
    {"click", "click_text", "switch_tab", "close_tab"}
)

# JavaScript helper: querySelector that pierces shadow DOM boundaries.
# Embed with: f"(() => {{ {_PIERCE_JS} const el = pierce(document, {safe_sel}); ... }})()"
_PIERCE_JS = (
    "function pierce(root,sel){"
    "let el;try{el=root.querySelector(sel);}catch(e){}"
    "if(el)return el;"
    "for(const n of root.querySelectorAll('*')){"
    "if(n.shadowRoot){el=pierce(n.shadowRoot,sel);if(el)return el;}}"
    "return null;}"
)


@dataclass
class StepResult:
    """Result of executing a single step."""
    action_desc: str
    success: bool
    retry_needed: bool
    attempts: int = 1
    failure_reason: str = ""
    status: str = ""
    evidence: list[OutcomeEvidence] = field(default_factory=list)


async def execute_step(
    step: str,
    dom_html: str,
    interactive_elements: str,
    llm: LLMClient,
    page: PageDomain,
    input_domain: InputDomain,
    runtime: RuntimeDomain,
    previous_failures: list[str] | None = None,
    current_url: str | None = None,
) -> StepResult:
    """
    Execute a single documentation step with post-action verification and retries.

    After each action, compares DOM state before/after. If nothing changed
    and the action was supposed to modify the page, retries with additional
    context about the failure. Max MAX_RETRIES attempts per step.

    Args:
        step: The step description to execute
        dom_html: Current page DOM HTML
        interactive_elements: JSON list of interactive elements
        llm: LLM client
        page: CDP Page domain
        input_domain: CDP Input domain
        runtime: CDP Runtime domain
        previous_failures: List of previous failure descriptions for retry context
        current_url: Current page URL for context awareness

    Returns:
        StepResult with success status and action description
    """
    failures = list(previous_failures or [])
    last_action_desc = ""
    all_evidence: list[OutcomeEvidence] = []

    for attempt in range(1, MAX_RETRIES + 1):
        # Capture DOM state BEFORE action
        text_before = await runtime.get_state_fingerprint()
        url_before = await runtime.evaluate("window.location.href")

        # Build prompt — include failure context on retries
        action = await _get_action(
            step, dom_html, interactive_elements, llm, failures, current_url
        )

        if action is None:
            logger.warning(f"  [Attempt {attempt}] Could not parse action for: {step}")
            failures.append("LLM returned unparseable action JSON")
            continue

        # Capture an event cursor before the action. PageDomain uses it to
        # evaluate only passive Network/Page evidence caused by this attempt.
        event_cursor = getattr(page, "event_cursor", 0)
        if not isinstance(event_cursor, int):
            event_cursor = 0

        # Execute the action
        action_desc = await _dispatch_action(action, page, input_domain, runtime)
        last_action_desc = action_desc
        try:
            expectations = _parse_expectations(action)
        except ValueError as exc:
            failures.append(f"Attempt {attempt}: invalid expectation: {exc}")
            continue

        action_type = action.get("action", "observe")

        # Wait for UI to settle. Explicit navigation barrier handles hard
        # navigations (process swap / app router transition) so the post-action
        # fingerprint runs against a live execution context, not a dead one.
        try:
            try:
                await page.wait_for_navigation_settled(
                    timeout=10.0, after_sequence=event_cursor
                )
            except TypeError:
                # Keep third-party PageDomain doubles made before T-2 usable.
                await page.wait_for_navigation_settled(timeout=10.0)
        except Exception:
            await asyncio.sleep(0.5)  # generic fallback

        evidence: list[OutcomeEvidence] = []
        # A UI mutation must not hide a failing API request.  The PageDomain
        # returns only method/status/query-less URL evidence; it never reads a
        # response body or enables Fetch interception.
        failures_since = getattr(page, "http_failures_since", None)
        if callable(failures_since):
            http_failures = failures_since(event_cursor)
            evidence.extend(
                OutcomeEvidence(
                    kind=item.kind,
                    passed=item.passed,
                    observed=item.observed,
                    details=item.details,
                )
                for item in http_failures
            )
            if http_failures:
                statuses = ", ".join(str(item.details.get("status")) for item in http_failures)
                failures.append(f"Attempt {attempt}: observed HTTP failure status {statuses}")
                logger.warning("  [Attempt %s] ✗ passive HTTP failure: %s", attempt, statuses)
                # Continue through the normal retry refresh below instead of
                # accepting a DOM change as proof of success.
                dom_html = await page.get_dom_html(max_length=4000)
                interactive_elements = await runtime.get_interactive_elements()
                all_evidence.extend(evidence)
                continue

        # Passive HTTP failure evidence is checked before an observe/wait can
        # be accepted as successful. A visible UI change is not evidence that
        # the backend operation succeeded.
        if action_type in ("observe", "wait") and not expectations:
            all_evidence.extend(evidence)
            return StepResult(
                action_desc=action_desc,
                success=True,
                retry_needed=False,
                attempts=attempt,
                status="completed",
                evidence=all_evidence,
            )

        if expectations:
            expectation_evidence = await _verify_expectations(
                page, expectations, after_sequence=event_cursor
            )
            evidence.extend(expectation_evidence)
            failed = [item for item in expectation_evidence if not item.passed]
            if not failed:
                all_evidence.extend(evidence)
                return StepResult(
                    action_desc=action_desc,
                    success=True,
                    retry_needed=False,
                    attempts=attempt,
                    status="completed",
                    evidence=all_evidence,
                )
            failures.append(
                f"Attempt {attempt}: expectation failed: "
                + "; ".join(item.observed for item in failed)
            )
            dom_html = await page.get_dom_html(max_length=4000)
            interactive_elements = await runtime.get_interactive_elements()
            all_evidence.extend(evidence)
            continue

        # Capture DOM state AFTER action — bounded so a dead/dying execution
        # context can never hang the whole pipeline. On timeout we treat the
        # action as having navigated (forcing url_changed=True), which is the
        # only way Runtime.evaluate could legitimately hang here anyway.
        try:
            text_after = await asyncio.wait_for(
                runtime.get_state_fingerprint(), timeout=5.0
            )
            url_after = await asyncio.wait_for(
                runtime.evaluate("window.location.href"), timeout=3.0
            )
        except asyncio.TimeoutError:
            logger.warning(
                "  Post-action state capture timed out — treating as navigation"
            )
            text_after = "<post-action-capture-timeout>"
            url_after = url_before + "#__nav__"
        except Exception as e:
            msg = str(e).lower()
            # Execution context destroyed mid-call is a navigation symptom —
            # treat like a timeout. Anything else is a real failure and must
            # stay retryable so the step isn't falsely reported as success.
            if (
                "execution context" in msg
                or "context was destroyed" in msg
                or "target closed" in msg
                or "no such execution context" in msg
            ):
                logger.warning(
                    f"  Post-action capture hit destroyed context ({e}) "
                    f"— treating as navigation"
                )
                text_after = "<post-action-capture-context-destroyed>"
                url_after = url_before + "#__nav__"
            else:
                raise

        # Check if something changed
        url_changed = url_before != url_after
        text_changed = (
            hashlib.md5(text_before.encode()).hexdigest()
            != hashlib.md5(text_after.encode()).hexdigest()
        )
        changed = url_changed or text_changed

        if changed:
            all_evidence.extend(evidence)
            logger.info(f"  [Attempt {attempt}] ✓ Action verified — DOM changed")
            return StepResult(
                action_desc=action_desc,
                success=True,
                retry_needed=False,
                attempts=attempt,
                status="completed",
                evidence=all_evidence,
            )

        # Action didn't change anything — might have failed silently
        if "[Failed]" in action_desc or "[Error]" in action_desc:
            failure_msg = f"Attempt {attempt}: {action_desc}"
            failures.append(failure_msg)
            logger.warning(f"  [Attempt {attempt}] ✗ Action failed: {action_desc}")
        else:
            failure_msg = (
                f"Attempt {attempt}: Executed '{action_desc}' but DOM did not change. "
                f"Element may be out of viewport, covered by a modal, or the selector is wrong."
            )
            failures.append(failure_msg)
            logger.warning(f"  [Attempt {attempt}] ✗ DOM unchanged after action")

        # Refresh DOM state for next attempt
        dom_html = await page.get_dom_html(max_length=4000)
        interactive_elements = await runtime.get_interactive_elements()

        # Try fallback: scroll element into view before retrying click
        if action_type == "click" and attempt < MAX_RETRIES:
            selector = action.get("selector", "")
            if selector:
                await _try_scroll_into_view(selector, runtime)
        all_evidence.extend(evidence)

    # All retries exhausted
    logger.error(f"  [FAILED] Step after {MAX_RETRIES} attempts: {step}")
    return StepResult(
        action_desc=f"[FAILED after {MAX_RETRIES} attempts] {last_action_desc}",
        success=False,
        retry_needed=False,
        attempts=MAX_RETRIES,
        failure_reason="; ".join(failures),
        status="failed",
        evidence=all_evidence,
    )


def _parse_expectations(action: dict[str, Any]) -> list[Expectation]:
    """Normalize the LLM action's optional ``expect``/``expects`` contract."""
    raw = action.get("expects", action.get("expect", []))
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError("expects must be an object or list of objects")
    return [Expectation.from_dict(item) for item in raw]


async def _verify_expectations(
    page: PageDomain,
    expectations: list[Expectation],
    *,
    after_sequence: int,
) -> list[OutcomeEvidence]:
    """Evaluate typed expectations through bounded PageDomain primitives."""
    evidence: list[OutcomeEvidence] = []
    for expectation in expectations:
        try:
            if expectation.kind == "url":
                passed = await page.wait_for_url(
                    expectation.value, mode=expectation.mode, timeout=expectation.timeout
                )
                evidence.append(OutcomeEvidence("url", passed, "URL matched" if passed else "URL did not match"))
            elif expectation.kind == "element":
                if not expectation.selector:
                    raise ValueError("element expectation requires selector")
                passed = await page.wait_for_element(
                    expectation.selector,
                    state=expectation.state,
                    timeout=expectation.timeout,
                )
                evidence.append(OutcomeEvidence("element", passed, f"element {expectation.state}" if passed else f"element not {expectation.state}"))
            elif expectation.kind == "text":
                passed = await page.wait_for_text(
                    expectation.value,
                    selector=expectation.selector,
                    timeout=expectation.timeout,
                )
                evidence.append(OutcomeEvidence("text", passed, "expected text visible" if passed else "expected text absent"))
            elif expectation.kind == "ax":
                passed = await page.wait_for_ax_text(expectation.value, timeout=expectation.timeout)
                evidence.append(OutcomeEvidence("ax", passed, "accessibility text present" if passed else "accessibility text absent"))
            elif expectation.kind == "network":
                item = await page.wait_for_http(
                    status=expectation.status,
                    url_contains=expectation.value or None,
                    timeout=expectation.timeout,
                    after_sequence=after_sequence,
                )
                evidence.append(
                    OutcomeEvidence(
                        "network",
                        item is not None and item.passed,
                        item.observed if item else "expected HTTP response not observed",
                        item.details if item else {},
                    )
                )
            elif expectation.kind == "download":
                item = await page.wait_for_download(
                    filename=expectation.value or None,
                    timeout=expectation.timeout,
                    after_sequence=after_sequence,
                )
                evidence.append(
                    OutcomeEvidence(
                        "download",
                        item is not None,
                        item.observed if item else "download not observed",
                        item.details if item else {},
                    )
                )
            elif expectation.kind == "networkIdle":
                await page.wait_for_network_idle(timeout=expectation.timeout, after_sequence=after_sequence)
                evidence.append(OutcomeEvidence("networkIdle", True, "network idle"))
            else:
                raise ValueError(f"unsupported expectation kind {expectation.kind!r}")
        except Exception as exc:  # A timeout is an outcome, not an executor crash.
            evidence.append(OutcomeEvidence(expectation.kind, False, f"expectation error: {exc}"))
    return evidence


async def _get_action(
    step: str,
    dom_html: str,
    interactive_elements: str,
    llm: LLMClient,
    failures: list[str],
    current_url: str | None = None,
) -> dict | None:
    """Ask LLM for the action to execute, including retry context."""
    url_context = f"CURRENT PAGE URL: {current_url}\n\n" if current_url else ""

    if failures:
        # Use retry prompt with failure context
        failure_context = "\n".join(f"  - {f}" for f in failures)
        user_prompt = (
            f"{url_context}"
            f"STEP TO EXECUTE: {step}\n\n"
            f"PREVIOUS ATTEMPTS FAILED:\n{failure_context}\n\n"
            f"Try a DIFFERENT approach. Consider:\n"
            f"- Use a different selector (try aria-label, data-testid, role, or XPath-style)\n"
            f"- The element might need scrolling into view first\n"
            f"- Try a JavaScript click as fallback\n\n"
            f"INTERACTIVE ELEMENTS:\n{interactive_elements}\n\n"
            f"PAGE HTML (truncated):\n{dom_html[:3000]}\n\n"
            f"Output the JSON action:"
        )
        system = EXECUTOR_RETRY_SYSTEM
    else:
        user_prompt = (
            f"{url_context}"
            f"STEP TO EXECUTE: {step}\n\n"
            f"INTERACTIVE ELEMENTS:\n{interactive_elements}\n\n"
            f"PAGE HTML (truncated):\n{dom_html[:3000]}\n\n"
            f"Output the JSON action to execute this step:"
        )
        system = EXECUTOR_SYSTEM

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_prompt},
    ]

    response = await llm.complete(messages, json_mode=True, role="executor")
    return _parse_action(response)


_TEXT_PSEUDO_RE = re.compile(
    r"""(?ix)
    (?:
      \[\s*text\s*=\s*['"]([^'"]+)['"]\s*\]   # [text='X']
      | :contains\(\s*['"]([^'"]+)['"]\s*\)   # :contains('X')
      | :has-text\(\s*['"]([^'"]+)['"]\s*\)   # :has-text('X')
    )
    """
)


def _extract_text_pseudo(selector: str) -> Optional[str]:
    """
    Detect non-CSS text pseudo-selectors that LLMs love to emit
    (`[text='X']`, `:contains('X')`, `:has-text('X')`) and return the
    inner text. Returns None if the selector is valid CSS.
    """
    if not selector:
        return None
    match = _TEXT_PSEUDO_RE.search(selector)
    if not match:
        return None
    return next((g for g in match.groups() if g), None)


async def _try_scroll_into_view(selector: str, runtime: RuntimeDomain) -> None:
    """Attempt to scroll an element into view before retrying a click."""
    try:
        safe_sel = json.dumps(selector)
        js = (
            f"(() => {{ {_PIERCE_JS} "
            f"pierce(document, {safe_sel})?.scrollIntoView({{behavior: 'smooth', block: 'center'}}); }})()"
        )
        await runtime.evaluate(js)
        await asyncio.sleep(0.1)  # tiny delay for UI to paint
        logger.debug(f"  Scrolled '{selector}' into view for retry")
    except Exception:
        pass


def _parse_action(response: str) -> dict | None:
    """Parse JSON action from LLM response."""
    try:
        return json.loads(response.strip())
    except json.JSONDecodeError:
        pass

    # Try to extract JSON from markdown code block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find any JSON object
    match = re.search(r"\{[^{}]+\}", response)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    logger.error(f"Failed to parse action JSON: {response[:200]}")
    return None


async def _dispatch_action(
    action: dict,
    page: PageDomain,
    input_domain: InputDomain,
    runtime: RuntimeDomain,
) -> str:
    """
    Execute a parsed action dict via CDP.

    Returns:
        Human-readable description of what was done
    """
    action_type = action.get("action", "observe")

    if action.get("target") is not None and action_type not in _TARGET_SCOPED_ACTIONS:
        return f"[Failed] action '{action_type}' does not support explicit target"

    try:
        if action_type == "click":
            selector = action["selector"]
            # LLMs frequently emit non-CSS pseudo-selectors like
            # `button[type='submit'][text='Entrar']`, `:contains("X")`, or
            # `:has-text("X")`. These never match. Detect them, extract the
            # text, and re-dispatch as click_text.
            text_match = _extract_text_pseudo(selector)
            if text_match is not None:
                logger.info(
                    "  Rewriting invalid selector %r → click_text(%r)",
                    selector,
                    text_match,
                )
                return await _dispatch_action(
                    {"action": "click_text", "text": text_match},
                    page,
                    input_domain,
                    runtime,
                )
            session_id, failure = await _resolve_action_session(action, page)
            if failure is not None:
                return f"[Failed] {failure}"
            success = await input_domain.click(selector, session_id=session_id)
            if success:
                return f"Clicked element: {selector}"
            # Fallback: try JS click with shadow DOM pierce
            safe_sel = json.dumps(selector)
            js_result = await runtime.evaluate(
                f"(() => {{ {_PIERCE_JS} "
                f"const el = pierce(document, {safe_sel}); "
                f"if(el) {{ el.click(); return true; }} return false; }})()",
                session_id=session_id,
            )
            if js_result:
                return f"Clicked element via JS fallback: {selector}"
            return f"[Failed] Element not found: {selector}"

        elif action_type == "click_text":
            # Fallback action: click by visible text
            text = action["text"]
            session_id, failure = await _resolve_action_session(action, page)
            if failure is not None:
                return f"[Failed] {failure}"
            safe_text = json.dumps(text)
            js = (
                f"(() => {{ "
                f"const els = [...document.querySelectorAll('a, button, [role=button], input[type=submit]')]; "
                f"const el = els.find(e => e.innerText?.trim().includes({safe_text})); "
                f"if(el) {{ el.click(); return true; }} return false; "
                f"}})()"
            )
            result = await runtime.evaluate(js, session_id=session_id)
            if result:
                return f"Clicked element by text: '{text}'"
            return f"[Failed] No element with text: '{text}'"

        elif action_type == "type":
            selector = action.get("selector")
            text = action["text"]
            await input_domain.type_text(text, selector=selector)

            # Determine if the element is sensitive for redaction in the action
            # description. Use the IIFE pattern (() => { ... })() so that
            # runtime.evaluate returns the result rather than the function
            # object, and embed the selector via json.dumps() for safe quoting
            # (matching the convention at lines 367/387/457).
            is_sensitive = False
            if selector is not None and selector != '':
                safe_sel = json.dumps(selector)
                element_attributes = await runtime.evaluate(
                    f"(() => {{ "
                    f"const el = document.querySelector({safe_sel}); "
                    f"if (!el) return null; "
                    f"return {{ name: el.name || '', autocomplete: el.autocomplete || '', type: el.type || '' }}; "
                    f"}})()"
                )
            else:
                # No selector means we are typing into the focused element
                element_attributes = await runtime.evaluate(
                    "(() => { "
                    "const el = document.activeElement; "
                    "if (!el) return null; "
                    "return { name: el.name || '', autocomplete: el.autocomplete || '', type: el.type || '' }; "
                    "})()"
                )

            if element_attributes:
                is_sensitive = is_sensitive_field(
                    name=element_attributes.get('name'),
                    autocomplete=element_attributes.get('autocomplete'),
                    field_type=element_attributes.get('type')
                )

            display_text = '***' if is_sensitive else text
            target = selector if selector else 'focused element'
            return f"Typed '{display_text}' into {target}"

        elif action_type == "press_key":
            key = action["key"]
            await input_domain.press_key(key)
            return f"Pressed key: {key}"

        elif action_type == "navigate":
            url = action["url"]
            await page.navigate(url)
            return f"Navigated to: {url}"

        elif action_type == "scroll":
            direction = action.get("direction", "down")
            delta_y = -400 if direction == "down" else 400
            await input_domain.scroll(delta_y=delta_y)
            return f"Scrolled {direction}"

        elif action_type == "scroll_to":
            selector = action["selector"]
            safe_sel = json.dumps(selector)
            await runtime.evaluate(
                f"(() => {{ {_PIERCE_JS} "
                f"pierce(document, {safe_sel})?.scrollIntoView({{behavior: 'smooth', block: 'center'}}); }})()"
            )
            await asyncio.sleep(0.1)  # tiny delay for UI to paint
            return f"Scrolled to element: {selector}"

        elif action_type == "wait":
            selector = action["selector"]
            found = await page.wait_for_selector(selector)
            return f"{'Found' if found else 'Timeout waiting for'}: {selector}"

        elif action_type == "observe":
            return "Observing current page state"

        elif action_type == "wait_for_popup":
            try:
                timeout = float(action.get("timeout", 10.0))
            except (TypeError, ValueError):
                return "[Failed] wait_for_popup timeout must be numeric"
            timeout = min(max(timeout, 1.0), 60.0)
            try:
                info = await page.wait_for_popup(timeout=timeout)
            except TimeoutError:
                return f"[Failed] No popup opened within {timeout:g}s"
            return f"Popup opened: {info['target_id'][:12]}..."

        elif action_type == "switch_tab":
            ref = action.get("target")
            if ref is None:
                return "[Failed] switch_tab requires 'target'"
            try:
                info = await page.switch_to_tab(ref)
            except RuntimeError as exc:
                return f"[Failed] {exc}"
            return f"Switched to tab: {info.get('url') or info['target_id'][:12]}"

        elif action_type == "close_tab":
            try:
                info = await page.close_tab(action.get("target"))
            except RuntimeError as exc:
                return f"[Failed] {exc}"
            return f"Closed tab: {info['closed'][:12]}..."

        else:
            logger.warning(f"Unknown action type: {action_type}")
            return f"[Unknown action: {action_type}]"

    except (WebSocketDisconnected, CircuitOpenError) as e:
        logger.error("CDP disconnect/circuit open mid-action (!)")
        raise e
    except websockets.exceptions.ConnectionClosed as e:
        logger.error("CDP Connection closed mid-action (!)")
        raise e
    except Exception as e:
        logger.error(f"Action execution error: {e}")
        return f"[Error] {action_type}: {e}"
