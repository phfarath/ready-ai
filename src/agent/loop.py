"""
Agentic Loop Orchestrator.

V2: Full Planner → Executor (with verification) → Critic (with re-execution) pipeline.
Supports authentication via cookies or credentials, and separate annotation model.
"""

from __future__ import annotations

import json
import logging
import re
import time
import websockets
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Collection, Optional

from ..llm.prompts import ANNOTATOR_PROMPT, PLANNER_SUPPLEMENT_SYSTEM
from ..docs.renderer import DocRenderer
from ..docs.output import save_docs
from ..cdp.connection_state import ConnectionState, RECONNECT_HEAL_WAIT_S
from ..cdp.exceptions import CircuitOpenError, WebSocketDisconnected
from ..observability import Span, init_run_context, get_metrics, log_event
from . import planner, executor, critic, recovery
from .cursor import CursorAnimator, extract_selector
from .browser_session import BrowserSession
from .state import RunState, DocStepState

if TYPE_CHECKING:
    from ..api.models import (
        FlowAction,
        FlowAssertion,
        FlowExtraction,
        FlowSpec,
        FlowStepSpec,
    )
    from ..cdp.input import InputDomain
    from ..cdp.page import PageDomain
    from ..cdp.runtime import RuntimeDomain
    from ..llm.client import LLMClient

logger = logging.getLogger(__name__)

# Hard cap on full browser respawns (BrowserSession.recover) per run.
# READY-AI-T-3: exceeding it terminates the run with a structured
# CircuitOpenError instead of retrying forever.
MAX_CRASHES = 3

# ─── Run-flow outcome classification (READY-AI-T-4 / fix B3) ─────────────
#
# ``executor._dispatch_action`` encodes failures in the description it
# returns: denial-prefixed wording (a failed lookup, an executor error,
# an unknown action type) or a "Timeout waiting for ..." signal from the
# ``wait`` action. Success, however, is only *verifiable* for actions that
# return an explicit non-denial description; for the silent-success actions
# below the executor has no failure wording at all, so their outcome is
# assumed passed without verification. Every other action that returns a
# description matching no denial prefix is fail-CLOSED ("unrecognized
# action outcome") instead of being guessed as passed.
KNOWN_DENIAL_PREFIXES: tuple[str, ...] = (
    "[failed]",
    "[error]",
    "[unknown",
    "timeout ",
)
KNOWN_SILENT_SUCCESS_ACTIONS: frozenset[str] = frozenset(
    {"scroll_to", "type", "press_key", "navigate"}
)

# ─── Explicit success wordings (slice-1 harness finding) ────────────────
# The executor's non-silent actions report success with fixed, explicit
# wording ("Clicked element: ...", "Found: ..."). The Fase-1 E2E harness
# proved these could never pass: the classifier below only trusted the
# silent set, so every `click` ended as "unrecognized action outcome".
# These prefixes are allowlisted verbatim — anything else still fails
# closed, and the step's asserts remain the real verifiers.
KNOWN_SUCCESS_PREFIXES: tuple[str, ...] = (
    "clicked element",
    "scrolled ",
    "found:",
    "found ",
    "observing current page state",
    "popup opened",
    "switched to tab",
    "closed tab",
)

# Text-bearing actions whose ``text`` parameter and executor description
# are redacted at the report boundary (fix B1).
_TEXT_MASKED_ACTIONS: frozenset[str] = frozenset({"type", "click_text"})


class AgenticLoop:
    """
    Main orchestrator for the agentic documentation generation pipeline.

    V2 enhancements:
    - Post-action verification in executor (StepResult with retries)
    - Critic can trigger re-execution of missing steps
    - Authentication support (cookies file or username/password)
    - Separate annotation model for cost optimization
    """

    def __init__(
        self,
        goal: str,
        url: str,
        model: str = "gpt-4o-mini",
        annotation_model: Optional[str] = None,
        output_dir: str = "./output",
        port: int = 9222,
        headless: bool = False,
        max_critic_rounds: int = 2,
        cookies_file: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        title: Optional[str] = None,
        language: Optional[str] = None,
        run_id: str = "local_run",
        resume_from: Optional[str] = None,
        plan_only: bool = False,
        app_version: Optional[str] = None,
        git_commit: Optional[str] = None,
        deployed_at: Optional[str] = None,
    ):
        self.run_id = run_id
        self.resume_from = resume_from
        self.plan_only = plan_only
        self.goal = goal
        self.title = title
        self.language = language
        self.url = url
        self.model = model
        self.annotation_model = annotation_model or model
        self.output_dir = output_dir
        self.headless = headless
        self.max_critic_rounds = max_critic_rounds
        self.app_version = app_version or ""
        self.git_commit = git_commit or ""
        self.deployed_at = deployed_at or ""

        self._session = BrowserSession(
            port=port,
            headless=headless,
            cookies_file=cookies_file,
            username=username,
            password=password,
        )
        self._cursor = CursorAnimator()
        self._last_url: Optional[str] = None
        self._max_replans_per_step = 2

        # READY-AI-T-3: full-respawn budget for the whole run (spanning
        # multiple `_execute_steps` invocations), so a flapping CDP
        # session can never trigger an unbounded respawn loop.
        self._connection_crashes: int = 0

        # Checkpointing state
        self._state_path = Path(self.output_dir) / f"{self.run_id}_state.json"

        if self.resume_from and Path(self.resume_from).exists():
            self._state = RunState.from_file(self.resume_from)
            if self._state:
                logger.info(f"Resuming run '{self._state.run_id}' from state file {self.resume_from}")
            else:
                self._state = RunState(
                    run_id=self.run_id, goal=self.goal, url=self.url,
                    app_version=self.app_version, git_commit=self.git_commit, deployed_at=self.deployed_at,
                )
        else:
            self._state = RunState(
                run_id=self.run_id, goal=self.goal, url=self.url,
                app_version=self.app_version, git_commit=self.git_commit, deployed_at=self.deployed_at,
            )

    def _save_checkpoint(self, status: Optional[str] = None) -> None:
        """Write current execution state to disk."""
        if status:
            self._state.status = status

        if self._last_url:
            self._state.last_known_url = self._last_url

        # Serialize document steps (including baselines for self-healing)
        if getattr(self, 'doc', None):
            self._state.doc_steps = [
                DocStepState(
                    number=s.step_number,
                    title=s.title,
                    action_description=s.action_description,
                    annotation=s.annotation,
                    screenshot_path=f"step_{s.step_number:02d}.png",
                    status=s.status,
                    status_reason=s.status_reason,
                    baseline_dom_hash=getattr(s, '_baseline_dom_hash', ''),
                    baseline_url=getattr(s, '_baseline_url', ''),
                ) for s in self.doc.steps
            ]

        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state.to_file(self._state_path)

    async def run(self) -> str:
        """
        Execute the full agentic documentation pipeline.

        Returns:
            Path to the generated markdown file
        """
        run_ctx = init_run_context(run_id=self.run_id)

        async with Span(name="pipeline", attributes={"goal": self.goal, "url": self.url}):
            try:
                from ..llm.client import LLMClient

                # 1. Launch Chrome and connect
                async with Span(name="browser_setup"):
                    await self._session.setup()

                # 2. Create domain helpers
                llm = LLMClient(model=self.model)
                annotation_llm = LLMClient(model=self.annotation_model)
                doc = DocRenderer(
                    goal=self.goal, title=self.title, language=self.language,
                    app_version=self.app_version, git_commit=self.git_commit,
                    deployed_at=self.deployed_at,
                )
                self.llm = llm
                self.annotation_llm = annotation_llm
                self.doc = doc

                # Enable page events
                await self._session.page.enable()

                # 3. Navigate to target URL FIRST. CDP Network.setCookie
                #    requires a valid origin to apply a cookie; injecting
                #    before navigate causes domain-less cookies to be
                #    silently dropped because no page is loaded yet.
                logger.info(f"═══ Navigating to: {self.url}")
                await self._session.page.navigate(self.url)

                # 4. Inject auth cookies now that the target origin is loaded
                if self._session.cookies_file:
                    await self._session.inject_cookies()

                # 5. Handle credential-based login if provided
                if self._session.username and self._session.password:
                    await self._session.handle_login(llm)

                # Start thinking cursor
                if not self.headless:
                    self._cursor.start(self._session.conn)

                # 5. Plan (or load from checkpoint)
                async with Span(name="planning"):
                    steps = await self._resolve_steps(llm, doc)

                if not steps:
                    raise RuntimeError("Planner returned no steps")

                if self.plan_only:
                    self._save_checkpoint("PLANNED")
                    self._log_plan(steps)
                    return str(self._state_path)

                # 6. Execute each step with verification
                async with Span(name="execute_steps"):
                    step_results = await self._execute_steps(steps, llm, annotation_llm, doc)

                # 7. Critic review with re-execution loop
                self._save_checkpoint("CRITIQUE")
                self._cursor.moving = True
                markdown = doc.render()
                async with Span(name="critic_loop"):
                    await self._critic_loop(markdown, llm, annotation_llm, doc, step_results)
                self._cursor.moving = False

                # 8. Save output
                self._save_checkpoint("FINISHED")
                markdown = doc.render()
                output_path = save_docs(
                    markdown,
                    doc.screenshots,
                    self.output_dir,
                    run_id=self.run_id,
                    goal=self.goal,
                    url=self.url,
                    model=self.model,
                    language=self.language or "en",
                    app_version=self.app_version,
                    git_commit=self.git_commit,
                    deployed_at=self.deployed_at,
                    status="FINISHED",
                )
                logger.info(f"═══ Documentation saved to: {output_path}")

                # Save metrics alongside output
                self._save_metrics(run_ctx, status="FINISHED", steps_planned=len(steps))

                return output_path
            except Exception as e:
                logger.error(f"Pipeline error: {e}")
                self._save_checkpoint("FAILED")
                self._save_metrics(run_ctx, status="FAILED")
                raise
            finally:
                await self._cursor.stop()
                await self._session.teardown()

    # ─── Docs-independent run-flow mode (READY-AI-T-4) ──────────────────
    #
    # Executes declarative YAML/JSON flows (actions, asserts, extractions,
    # retries) over the SAME browser/CDP core used by the documentation
    # pipeline — and, unlike `run()`, never instantiates DocRenderer or any
    # screenshot/annotation component. Each step is reported with its
    # actions (including retry attempts), expectations, and extracted data.

    async def run_flow(
        self,
        flow: FlowSpec,
        *,
        confirm: Collection[str] | None = None,
    ) -> dict:
        """
        Execute a declarative run-flow and return a structured JSON result.

        Flow steps declare concrete actions (dispatched through
        ``executor._dispatch_action`` — the same CDP executor core the
        documentation path uses), expectations (asserts), extractions,
        and retry budgets. Unlike ``run()`` this mode:

          * never instantiates DocRenderer / screenshots / vision annotation
          * never calls the LLM (except credential login when provided)
          * reports per step: actions + attempts, asserts (expected/actual),
            extracted data, and the step's overall retry accounting

        A step aborts after the first action whose retry budget is
        exhausted, so the result pinpoints the root cause instead of
        cascading noise; remaining actions/asserts/extractions of that
        step are reported via ``skipped_asserts``/``skipped_extractions``
        instead of being silently dropped.

        A CDP disconnection (surfacing as a "CDP connection lost during
        action" failure) is run-level and terminal: the remaining steps
        are never executed and are reported as ``skipped`` with reason
        "aborted: CDP connection lost", ``failure_reason`` on the result
        carries the sanitized disconnect reason, and the summary counts
        only executed steps plus the explicitly skipped remainder — the
        result stays truthful about what did and did not run.

        Returns:
            JSON-serializable dict with keys: run_id, flow, url, status,
            steps[], summary{}, failure_reason
        """
        run_ctx = init_run_context(run_id=self.run_id)
        flow_name = flow.name or "run-flow"
        step_results: list[dict] = []
        overall_status = "passed"
        run_failed_reason: Optional[str] = None
        confirmations = frozenset(confirm or ())

        async with Span(name="flow_run", attributes={"url": self.url, "flow": flow_name}):
            try:
                async with Span(name="browser_setup"):
                    await self._session.setup()

                page = self._session.page
                runtime = self._session.runtime
                input_domain = self._session.input_domain

                await page.enable()
                logger.info("═══ Run-flow '%s' — navigating to: %s", flow_name, self.url)
                await page.navigate(self.url)

                if self._session.cookies_file:
                    await self._session.inject_cookies()

                if self._session.username and self._session.password:
                    llm = LLMClient(model=self.model)
                    await self._session.handle_login(llm)

                try:
                    await page.wait_for_network_idle(timeout=10.0, idle_time=0.5)
                except Exception as exc:
                    logger.debug(f"Flow start network-idle wait failed/timed out: {exc}")

                for index, step in enumerate(flow.steps, start=1):
                    step_report = await self._execute_flow_step(
                        step,
                        index,
                        flow.retries,
                        page,
                        input_domain,
                        runtime,
                        flow_policy=flow.effect_policy,
                        confirmations=confirmations,
                    )
                    step_results.append(step_report)
                    if step_report["status"] == "failed":
                        overall_status = "failed"
                    elif step_report["status"] == "pending_confirmation" and overall_status == "passed":
                        overall_status = "pending_confirmation"
                    if overall_status == "pending_confirmation" and run_failed_reason is None:
                        run_failed_reason = step_report["failure_reason"]
                    # A CDP disconnection is run-level and terminal: stop
                    # executing, report the remaining steps as skipped.
                    disconnect_reason = self._disconnect_failure_reason(step_report)
                    if disconnect_reason:
                        run_failed_reason = disconnect_reason
                        self._mark_remaining_steps_skipped(step_results, flow, index)
                        break

                self._state.status = "FINISHED" if overall_status == "passed" else "FAILED"
                self._save_checkpoint()

                result = self._build_flow_result(
                    flow, flow_name, overall_status, step_results, run_failed_reason
                )
                self._persist_flow_result(result)
                self._save_flow_metrics(run_ctx, flow_name, overall_status, step_results)
                return result
            except Exception as exc:
                logger.error(f"Run-flow failed: {exc}")
                self._state.status = "FAILED"
                try:
                    self._save_checkpoint()
                except Exception:
                    pass
                raise
            finally:
                await self._session.teardown()

    async def _execute_flow_step(
        self,
        step: FlowStepSpec,
        index: int,
        default_retries: int,
        page: PageDomain,
        input_domain: InputDomain,
        runtime: RuntimeDomain,
        *,
        flow_policy: str = "write",
        confirmations: frozenset[str] = frozenset(),
    ) -> dict:
        """Execute one declarative step and build its structured report.

        Policy gates run BEFORE any browser actuation, in order:
          1. idempotent replay — key already in ``state.confirmed_effects``
             reports passed without executing;
          2. ceiling — every declared action must sit at or below the step
             policy (inherited from the flow); violations abort fail-closed;
          3. confirmation — a ``confirm`` step whose key was not passed via
             ``run_flow(confirm={...})`` reports pending_confirmation.

        Actions run next (each with its retry budget). If an action
        exhausts its budget the step is aborted and the remaining
        actions/asserts/extractions are not evaluated, so the failure
        reason is unambiguous. Otherwise asserts and extractions run and
        are reported per step.
        """
        key = step.idempotency_key or f"{self.run_id}:step-{index}"
        if key in self._state.confirmed_effects:
            return {
                "index": index,
                "name": step.name,
                "actions": [],
                "asserts": [],
                "extracted": [],
                "attempts": 0,
                "status": "passed",
                "failure_reason": "",
                "skipped_asserts": 0,
                "skipped_extractions": 0,
                "idempotency_key": key,
                "confirmation": "idempotent-replay",
            }

        ceiling = step.policy or flow_policy
        for action in step.actions:
            atype = str(getattr(action, "action", "observe"))
            if not executor.action_allowed_under_policy(atype, ceiling):
                return {
                    "index": index,
                    "name": step.name,
                    "actions": [],
                    "asserts": [],
                    "extracted": [],
                    "attempts": 0,
                    "status": "failed",
                    "failure_reason": (
                        f"action '{atype}' exceeds policy ceiling '{ceiling}' "
                        f"(step {index}); nothing was executed"
                    ),
                    "skipped_asserts": len(step.asserts),
                    "skipped_extractions": len(step.extract),
                    "idempotency_key": key,
                }

        if step.confirm and key not in confirmations:
            return {
                "index": index,
                "name": step.name,
                "actions": [],
                "asserts": [],
                "extracted": [],
                "attempts": 0,
                "status": "pending_confirmation",
                "failure_reason": (
                    f"step {index} requires explicit confirmation "
                    f"(idempotency_key='{key}'); nothing was executed"
                ),
                "skipped_asserts": len(step.asserts),
                "skipped_extractions": len(step.extract),
                "idempotency_key": key,
            }

        step_retries = step.retries if step.retries is not None else default_retries
        action_reports: list[dict] = []
        reasons: list[str] = []
        aborted = False

        for action in step.actions:
            report = await self._execute_flow_action(
                action, step_retries, page, input_domain, runtime
            )
            action_reports.append(report)
            if not report["passed"]:
                reasons.append(
                    report["failure_reason"]
                    or report["description"]
                    or f"action '{report['action']}' failed"
                )
                aborted = True
                break

        assert_results: list[dict] = []
        extracted: list[dict] = []
        # DoD2: declared-but-not-executed asserts/extractions are counted
        # instead of being silently dropped when the step aborts.
        skipped_asserts = 0
        skipped_extractions = 0
        if not aborted:
            for assertion in step.asserts:
                target_session: Optional[str] = None
                target_ref = getattr(assertion, "target", None)
                if target_ref is not None:
                    try:
                        target_session = runtime.resolve_target_session(target_ref)
                    except RuntimeError as exc:
                        assert_results.append(
                            {
                                "type": assertion.type,
                                "selector": assertion.selector,
                                "expected": assertion.expected,
                                "actual": None,
                                "passed": False,
                                "message": str(exc),
                            }
                        )
                        reasons.append(str(exc))
                        continue
                result = await self._evaluate_flow_assertion(
                    assertion, runtime, target_session=target_session
                )
                assert_results.append(result)
                if not result["passed"]:
                    reasons.append(result["message"] or f"assert '{result['type']}' failed")

            for extraction in step.extract:
                value = await self._extract_flow_value(extraction, runtime)
                extracted.append(
                    {
                        "name": extraction.name,
                        "selector": extraction.selector,
                        "value": value,
                    }
                )
        else:
            skipped_asserts = len(step.asserts)
            skipped_extractions = len(step.extract)

        attempts = max((r["attempts"] for r in action_reports), default=1)
        status = (
            "failed"
            if aborted or any(not a["passed"] for a in assert_results)
            else "passed"
        )
        report: dict = {
            "index": index,
            "name": step.name,
            "actions": action_reports,
            "asserts": assert_results,
            "extracted": extracted,
            "attempts": attempts,
            "status": status,
            "failure_reason": "; ".join(dict.fromkeys(reasons)) if reasons else "",
            "skipped_asserts": skipped_asserts,
            "skipped_extractions": skipped_extractions,
            "idempotency_key": key,
        }
        if status == "passed" and step.confirm and key not in self._state.confirmed_effects:
            self._state.confirmed_effects.append(key)
            self._save_checkpoint()
        return report

    async def _execute_flow_action(
        self,
        action: FlowAction,
        default_retries: int,
        page: PageDomain,
        input_domain: InputDomain,
        runtime: RuntimeDomain,
    ) -> dict:
        """Dispatch one declarative action with a bounded retry budget.

        Retries re-run the SAME declared action (deterministic flow
        semantics) through ``executor._dispatch_action`` — the executor
        core shared with the documentation pipeline. A CDP disconnect
        surfaces as a failed report instead of hanging the flow.
        """
        payload = {
            k: v
            for k, v in {
                **action.model_dump(exclude_unset=True),
                **(action.model_extra or {}),
            }.items()
            if v is not None
        }
        payload.pop("retries", None)
        action_type = str(payload.get("action", "observe"))
        budget = max(0, action.retries if action.retries is not None else default_retries)
        params = self._flow_params_for_report(payload)

        attempts = 0
        last_desc = ""
        last_failure = ""
        while attempts <= budget:
            attempts += 1
            try:
                desc = await executor._dispatch_action(
                    payload, page, input_domain, runtime
                )
            except (websockets.exceptions.ConnectionClosed, WebSocketDisconnected) as exc:
                last_failure = f"CDP connection lost during action: {exc}"
                break
            except Exception as exc:
                last_failure = f"Action error: {exc}"
                break
            last_desc = desc or ""
            if self._flow_action_ok(last_desc, action_type):
                return {
                    "action": action_type,
                    "params": params,
                    "description": self._mask_flow_description(
                        last_desc, action_type, payload
                    ),
                    "attempts": attempts,
                    "passed": True,
                    "failure_reason": "",
                }
            last_failure = self._flow_failure_reason(last_desc, action_type)

        return {
            "action": action_type,
            "params": params,
            "description": self._mask_flow_description(last_desc, action_type, payload),
            "attempts": max(attempts, 1),
            "passed": False,
            "failure_reason": last_failure,
        }

    @staticmethod
    def _flow_action_ok(description: str, action_type: str) -> bool:
        """Classify a dispatch description as passed, failing CLOSED.

        Success is only assumed for actions whose executor wording cannot
        express failure (``KNOWN_SILENT_SUCCESS_ACTIONS``), plus the
        allowlisted explicit success wordings (``KNOWN_SUCCESS_PREFIXES``,
        e.g. the executor's "Clicked element: ..." — the Fase-1 harness
        proved clicks could never pass without this). Every other
        description matching no denial prefix is fail-CLOSED ("unrecognized
        action outcome") instead of being guessed as passed.
        """
        if not description:
            return False
        lowered = description.lower()
        for prefix in KNOWN_DENIAL_PREFIXES:
            if lowered.startswith(prefix):
                return False
        if action_type in KNOWN_SILENT_SUCCESS_ACTIONS:
            return True
        return lowered.startswith(KNOWN_SUCCESS_PREFIXES)

    @staticmethod
    def _flow_failure_reason(description: str, action_type: str) -> str:
        """Human-readable failure reason for a non-passing description."""
        if not description:
            return "empty action outcome"
        lowered = description.lower()
        for prefix in KNOWN_DENIAL_PREFIXES:
            if lowered.startswith(prefix):
                return description
        return "unrecognized action outcome"

    @staticmethod
    def _flow_params_for_report(payload: dict) -> dict:
        """Build report params; typed text is masked to avoid leaking secrets."""
        params = {k: v for k, v in payload.items() if k != "action"}
        if payload.get("action") in _TEXT_MASKED_ACTIONS and "text" in params:
            params["text"] = "***"
        return params

    @staticmethod
    def _mask_flow_description(description: str, action_type: str, payload: dict) -> str:
        """Redact raw typed/clicked text from the executor description.

        Display-level redaction at the report boundary: the executor's own
        locally logged description (out of scope here) may carry the raw
        value — e.g. when ``is_sensitive_field`` misses the target field —
        so every occurrence of the raw value in the description is replaced
        with ``"***"`` before it enters a report. Empty/whitespace text is
        left untouched.
        """
        if action_type not in _TEXT_MASKED_ACTIONS:
            return description
        raw = payload.get("text")
        if not isinstance(raw, str) or not raw.strip():
            return description
        return re.sub(re.escape(raw), "***", description)

    @staticmethod
    def _disconnect_failure_reason(step_report: dict) -> Optional[str]:
        """Return a sanitized run-level reason when a step hit a CDP disconnect.

        ``_execute_flow_action`` marks every disconnect (a re-raised
        ``websockets.ConnectionClosed`` / ``WebSocketDisconnected`` from
        ``_dispatch_action``, or a ``CircuitOpenError`` surfaced outside
        it) with the "CDP connection lost during action:" marker. The
        exception payload itself is dropped so no URLs or credentials
        leak into the run-level result.
        """
        for action_report in step_report.get("actions", []):
            reason = action_report.get("failure_reason") or ""
            if "CDP connection lost" in reason:
                action_type = action_report.get("action", "unknown")
                return f"CDP connection lost during action: {action_type}"
        return None

    @staticmethod
    def _mark_remaining_steps_skipped(
        step_results: list[dict],
        flow: FlowSpec,
        failed_index: int,
    ) -> None:
        """Append truthful ``skipped`` reports for steps after a CDP abort.

        ``failed_index`` is the 1-based index of the step that failed on
        the disconnect; the remaining declared steps are never executed
        and report status "skipped" with an explicit abort reason, and
        their declared asserts/extractions are counted as skipped.
        """
        for j in range(failed_index, len(flow.steps)):
            step = flow.steps[j]
            step_results.append(
                {
                    "index": j + 1,
                    "name": step.name,
                    "actions": [],
                    "asserts": [],
                    "extracted": [],
                    "attempts": 0,
                    "status": "skipped",
                    "failure_reason": "aborted: CDP connection lost",
                    "skipped_asserts": len(step.asserts),
                    "skipped_extractions": len(step.extract),
                }
            )

    async def _evaluate_flow_assertion(
        self,
        assertion: FlowAssertion,
        runtime: RuntimeDomain,
        *,
        target_session: Optional[str] = None,
    ) -> dict:
        """Evaluate one declarative expectation, optionally in another tab."""
        atype = assertion.type
        selector = assertion.selector
        expected = assertion.expected
        actual: object = None
        passed = False
        # B5: empty-expected comparisons that would otherwise trivially pass
        # (e.g. url_contains with no expected, or an equality assert against
        # a missing element with expected "") must fail closed with a clear
        # message.
        guard_message = ""
        try:
            if atype in ("url_contains", "url_equals", "not_url_contains"):
                actual = await runtime.evaluate(
                    "window.location.href", session_id=target_session
                )
                actual_s = str(actual or "")
                exp_s = str(expected or "")
                if atype == "url_contains":
                    if expected is None or str(expected).strip() == "":
                        passed = False
                        guard_message = "empty expected value"
                    else:
                        passed = exp_s in actual_s
                elif atype == "url_equals":
                    passed = actual_s == exp_s
                else:
                    passed = exp_s not in actual_s
            elif atype in ("element_present", "element_missing"):
                if not selector:
                    passed = False
                else:
                    actual = await runtime.query_selector(
                        selector, session_id=target_session
                    )
                    found = actual is not None
                    passed = found if atype == "element_present" else not found
            elif atype == "element_visible":
                if not selector:
                    passed = False
                else:
                    safe_sel = json.dumps(selector)
                    js = (
                        f"(() => {{ const el = document.querySelector({safe_sel}); "
                        f"if (!el) return false; const r = el.getBoundingClientRect(); "
                        f"if (r.width === 0 || r.height === 0) return false; "
                        f"const s = window.getComputedStyle(el); "
                        f"return s.visibility !== 'hidden' && s.display !== 'none'; }})()"
                    )
                    actual = await runtime.evaluate(js, session_id=target_session)
                    passed = bool(actual)
            elif atype in ("text_contains", "text_equals"):
                if selector:
                    actual = await runtime.get_element_text(
                        selector, session_id=target_session
                    )
                else:
                    actual = await runtime.get_visible_text(session_id=target_session)
                actual_s = str(actual or "")
                if atype == "text_contains":
                    passed = str(expected or "") in actual_s
                else:
                    if (expected is None or str(expected).strip() == "") and actual_s.strip() == "":
                        passed = False
                        guard_message = "empty expected value (element text missing)"
                    else:
                        passed = actual_s.strip() == str(expected or "").strip()
            elif atype == "attribute_equals":
                if not selector or not assertion.attribute:
                    passed = False
                else:
                    attrs = await runtime.get_element_attributes(
                        selector, session_id=target_session
                    )
                    actual = attrs.get(assertion.attribute)
                    if (expected is None or str(expected).strip() == "") and actual is None:
                        passed = False
                        guard_message = "empty expected value (attribute missing)"
                    else:
                        passed = str(actual or "") == str(expected or "")
            else:
                raise ValueError(f"Unsupported flow assertion type: {atype}")
        except Exception as exc:
            passed = False
            actual = None
            message = f"{atype} errored: {exc}"
            return {
                "type": atype,
                "selector": selector,
                "expected": expected,
                "actual": actual,
                "passed": passed,
                "message": message,
            }

        message = ""
        if not passed:
            message = assertion.message or (
                guard_message
                or f"{atype} failed: expected={expected!r} actual={actual!r}"
            )
        return {
            "type": atype,
            "selector": selector,
            "expected": expected,
            "actual": actual,
            "passed": passed,
            "message": message,
        }

    async def _extract_flow_value(
        self,
        extraction: FlowExtraction,
        runtime: RuntimeDomain,
    ) -> object:
        """Read one extracted value (or list) from the live page."""
        safe_sel = json.dumps(extraction.selector)
        safe_prop = json.dumps(extraction.attribute or "textContent")
        multi = "true" if extraction.multiple else "false"
        js = f"""(() => {{
            const els = Array.from(document.querySelectorAll({safe_sel}));
            if (els.length === 0) return {multi} ? [] : null;
            const read = (el) => {{
                let key = {safe_prop};
                if (key.startsWith('@')) return el.getAttribute(key.slice(1));
                try {{
                    if (key in el) {{
                        const v = el[key];
                        if (v === undefined) return null;
                        if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return v;
                        return null;
                    }}
                }} catch (e) {{ }}
                return el.getAttribute(key);
            }};
            const values = els.map(read);
            return {multi} ? values : values[0];
        }})()"""
        try:
            return await runtime.evaluate(js)
        except Exception as exc:
            logger.warning(f"Flow extraction '{extraction.name}' failed: {exc}")
            return None

    def _build_flow_result(
        self,
        flow: FlowSpec,
        flow_name: str,
        status: str,
        step_results: list[dict],
        failure_reason: Optional[str] = None,
    ) -> dict:
        """Assemble the structured JSON result with a run summary."""
        summary = {
            "steps_total": len(flow.steps),
            "steps_passed": sum(1 for s in step_results if s["status"] == "passed"),
            "steps_failed": sum(1 for s in step_results if s["status"] == "failed"),
            "steps_skipped": sum(1 for s in step_results if s["status"] == "skipped"),
            "actions_total": sum(len(s["actions"]) for s in step_results),
            "actions_failed": sum(
                1 for s in step_results for a in s["actions"] if not a["passed"]
            ),
            "asserts_total": sum(len(s["asserts"]) for s in step_results),
            "asserts_failed": sum(
                1 for s in step_results for a in s["asserts"] if not a["passed"]
            ),
            "extractions": sum(len(s["extracted"]) for s in step_results),
            "attempts_total": sum(int(s["attempts"]) for s in step_results),
            "retries_used": sum(
                max(0, int(a["attempts"]) - 1)
                for s in step_results
                for a in s["actions"]
            ),
            "skipped_asserts_total": sum(
                int(s["skipped_asserts"]) for s in step_results
            ),
            "skipped_extractions_total": sum(
                int(s["skipped_extractions"]) for s in step_results
            ),
        }
        return {
            "run_id": self.run_id,
            "flow": flow_name,
            "url": self.url,
            "status": status,
            "steps": step_results,
            "summary": summary,
            "failure_reason": failure_reason,
        }

    def _persist_flow_result(self, result: dict) -> None:
        """Write the flow result JSON alongside the run state output."""
        try:
            out = Path(self.output_dir)
            out.mkdir(parents=True, exist_ok=True)
            path = out / f"{self.run_id}_flow_result.json"
            path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
            logger.info("═══ Flow result saved to: %s", path)
        except Exception as exc:
            logger.warning(f"Failed to save flow result: {exc}")

    def _save_flow_metrics(
        self,
        run_ctx,
        flow_name: str,
        status: str,
        step_results: list[dict],
    ) -> None:
        """Write flow run metrics to a JSON file alongside the result."""
        try:
            summary = run_ctx.run_summary(
                status=status.upper(),
                flow=flow_name,
                steps_planned=len(step_results),
            )
            metrics_path = Path(self.output_dir) / f"{self.run_id}_flow_metrics.json"
            metrics_path.parent.mkdir(parents=True, exist_ok=True)
            metrics_path.write_text(
                json.dumps(summary, indent=2, default=str), encoding="utf-8"
            )
            logger.info("═══ Flow metrics saved to: %s", metrics_path)
        except Exception as exc:
            logger.warning(f"Failed to save flow metrics: {exc}")

    def _save_metrics(self, run_ctx, status: str = "FINISHED", **extra) -> None:
        """Write run metrics to a JSON file alongside the output."""
        try:
            summary = run_ctx.run_summary(status=status, **extra)
            metrics_path = Path(self.output_dir) / f"{self.run_id}_metrics.json"
            metrics_path.parent.mkdir(parents=True, exist_ok=True)
            metrics_path.write_text(json.dumps(summary, indent=2, default=str))
            # `summary` may already contain an 'event' key from run_ctx —
            # strip it so it doesn't collide with log_event's first arg.
            summary_payload = {k: v for k, v in summary.items() if k != "event"}
            log_event("run_complete", **summary_payload)
            logger.info(f"═══ Metrics saved to: {metrics_path}")
        except Exception as exc:
            logger.warning(f"Failed to save metrics: {exc}")

    def _restore_doc_from_state(self, doc: DocRenderer) -> None:
        """Restore rendered step metadata from checkpoint state."""
        if not self._state.doc_steps:
            return
        for ds in self._state.doc_steps:
            doc.add_step(
                step_number=ds.number,
                title=ds.title,
                screenshot_b64="",
                annotation=ds.annotation,
                action_description=ds.action_description,
                status=ds.status,
                status_reason=ds.status_reason,
            )
            # Restore baselines from checkpoint
            if doc.steps:
                doc.steps[-1]._baseline_dom_hash = ds.baseline_dom_hash
                doc.steps[-1]._baseline_url = ds.baseline_url

    def _log_plan(self, steps: list[str]) -> None:
        """Log a numbered plan for plan-only mode."""
        logger.info("═══ Planned steps")
        for index, step in enumerate(steps, 1):
            logger.info("    %s. %s", index, step)

    async def _resolve_steps(self, llm: LLMClient, doc: DocRenderer) -> list[str]:
        """Load a saved plan or generate a new one for the current page state."""
        has_saved_plan = bool(self._state.planned_steps)
        should_resume_plan = has_saved_plan and (
            self.plan_only or self._state.current_step_index < len(self._state.planned_steps)
        )

        if should_resume_plan:
            logger.info(
                "═══ Resuming planned steps from checkpoint (%s/%s)",
                self._state.current_step_index,
                len(self._state.planned_steps),
            )
            self._restore_doc_from_state(doc)
            return self._state.planned_steps

        logger.info("═══ Planning steps for: %s", self.goal)
        self._save_checkpoint("PLANNING")
        self._cursor.moving = True
        page = self._session.page
        runtime = self._session.runtime
        dom_html = await page.get_dom_html(max_length=4000)
        elements = await runtime.get_interactive_elements()
        steps = await planner.plan(self.goal, dom_html, elements, llm, language=self.language)
        self._cursor.moving = False

        self._state.planned_steps = steps
        self._state.current_step_index = 0
        self._state.executed_results = []
        self._state.doc_steps = []
        self._save_checkpoint("PLANNED" if self.plan_only else "EXECUTING")
        return steps

    async def _get_page_context(self, max_length: int = 4000) -> tuple[str, str, str]:
        """Get DOM HTML, interactive elements, and current URL in one call."""
        page = self._session.page
        runtime = self._session.runtime
        dom_html = await page.get_dom_html(max_length=max_length)
        elements = await runtime.get_interactive_elements()
        url = await runtime.evaluate("window.location.href")
        return dom_html, elements, url

    @staticmethod
    def _format_step_action_details(result: executor.StepResult) -> str:
        details = result.action_desc
        if result.failure_reason:
            details += f"\n\n**Failure details:** {result.failure_reason}"
        return details

    async def _recover_session_after_disconnect(
        self,
        exc: Exception,
        llm: LLMClient,
        step_idx: int,
    ) -> None:
        """Unified CDP disconnect recovery (READY-AI-T-3).

        Both disconnect exception types — `websockets.exceptions.
        ConnectionClosed` and our `WebSocketDisconnected` (including
        its structured `CircuitOpenError` subtype) — land in this ONE
        recovery path, so the loop no longer depends on which subtype
        the connection layer happened to raise.

        Recovery ladder:

          1. Bounded auto-reconnect wait: while the connection's own
             reconnect+reattach is still in flight (DEGRADED), give it
             up to `RECONNECT_HEAL_WAIT_S` to heal. On success the
             in-flight step resumes on the same session — no Chrome
             respawn, no checkpoint reset.
          2. Full respawn: when the circuit is open (DOWN) or the heal
             wait timed out, fall back to `BrowserSession.recover()`.
             Respawns are bounded by `MAX_CRASHES`; exceeding the
             budget raises a structured `CircuitOpenError` instead of
             retrying forever — terminal failures are never masked.

        Step context is preserved by construction: this method never
        touches ``self._state.current_step_index``, so the caller's
        while-loop re-runs only the in-flight step; completed
        (checkpointed) steps are never re-executed. Cancellation is
        never swallowed: `asyncio.CancelledError` propagates so the
        caller can tear down cleanly.
        """
        logger.warning(
            "CDP session disconnected during step %d: %r",
            step_idx + 1,
            exc,
        )
        session = self._session
        conn = session.conn

        # 1. In-flight auto-reconnect? Give the connection a bounded
        #    chance to heal itself before we consider a full respawn.
        if (
            conn is not None
            and conn.state == ConnectionState.DEGRADED
            and session.is_reconnecting
        ):
            final_state = await session.wait_for_reconnect(
                timeout=RECONNECT_HEAL_WAIT_S, poll_interval=0.1
            )
            if final_state == ConnectionState.HEALTHY:
                metrics = get_metrics()
                if metrics:
                    metrics.increment("recovery.reattach")
                logger.info(
                    "⟲ CDP reconnected and reattached in place; "
                    "resuming step %d on the same session",
                    step_idx + 1,
                )
                return
        # 2. Full respawn, bounded by the crash budget.
        self._connection_crashes += 1
        if self._connection_crashes > MAX_CRASHES:
            state = conn.state.value if conn is not None else "unknown"
            metrics = get_metrics()
            if metrics:
                metrics.increment("recovery.exhausted")
            logger.error(
                "⟲ Exceeded maximum global CDP recoveries (%s). "
                "Aborting pipeline.",
                MAX_CRASHES,
            )
            raise CircuitOpenError(
                f"CDP session unrecoverable after {MAX_CRASHES} respawn "
                f"attempts (connection state: {state})",
                state=state,
                attempts=self._connection_crashes,
                step=step_idx,
            )
        resume_url = self._last_url or self.url
        logger.info(
            "⟲ Full browser respawn after CDP disconnect "
            "(respawn %d/%d)",
            self._connection_crashes,
            MAX_CRASHES,
        )
        await session.recover(resume_url, llm)

    async def _execute_steps(
        self,
        steps: list[str],
        llm: LLMClient,
        annotation_llm: LLMClient,
        doc: DocRenderer,
        start_number: int = 1,
        start_index: Optional[int] = None,
    ) -> list[executor.StepResult]:
        """Execute a list of steps with verification, screenshots, and annotations.

        ``start_index`` seeds the first step to execute within ``steps``.
        The default (None) resolves the checkpoint index via
        ``recovery.resume_step_index`` so the MAIN plan never
        re-executes confirmed steps after a crash/resume. Sub-plan /
        critic re-execution passes ``start_index=0``: those runs are
        independent of the main-plan cursor, which would otherwise
        silently skip their first missing step once it is nonzero.
        """
        results = []
        self._last_url = None

        page = self._session.page
        input_domain = self._session.input_domain
        runtime = self._session.runtime

        step_list = list(steps)
        # READY-AI-T-3: resume from the last valid checkpoint so
        # completed steps are never re-executed on recovery — valid
        # ONLY for the main-plan path. Sub-plan/critic executions pass
        # an explicit start_index and skip this resolution (their first
        # step must never be skipped because the main cursor moved).
        if start_index is None:
            step_idx = recovery.resume_step_index(self._state, len(step_list))
        else:
            step_idx = start_index
        i = start_number + step_idx
        replan_attempts_by_index: dict[int, int] = {}

        while step_idx < len(step_list):
            try:
                step = step_list[step_idx]
                step_start = time.monotonic()
                logger.info(f"═══ Step {i}: {step}")

                # Get fresh DOM state and current URL
                dom_html = await page.get_dom_html(max_length=4000)
                elements = await runtime.get_interactive_elements()
                pre_url = await runtime.evaluate("window.location.href")
                pre_fingerprint = await recovery.dom_fingerprint(runtime)

                # URL drift detection with replanning
                if self._last_url is not None and self._last_url != pre_url:
                    logger.warning(
                        f"    ⚠ URL changed between steps: {self._last_url} → {pre_url}"
                    )
                    remaining = step_list[step_idx:]
                    replanned = await recovery.replan_remaining(
                        remaining, dom_html, elements, pre_url, llm,
                        language=self.language,
                    )
                    if replanned:
                        logger.info(
                            f"    ⟳ Replanned {len(remaining)} remaining steps "
                            f"→ {len(replanned)} adapted steps"
                        )
                        step_list = step_list[:step_idx] + replanned
                        self._state.planned_steps = step_list
                        self._save_checkpoint("EXECUTING")
                        step = step_list[step_idx]

                self._last_url = pre_url
                self._cursor.moving = False

                # Execute with verification + retries
                result = await executor.execute_step(
                    step, dom_html, elements, llm, page, input_domain, runtime,
                    current_url=pre_url,
                )

                logger.info(
                    f"    {'✓' if result.success else '✗'} {result.action_desc} "
                    f"(attempts: {result.attempts})"
                )

                # Wait for UI and network to settle
                try:
                    await page.wait_for_network_idle(timeout=10.0, idle_time=0.5)
                except Exception as e:
                    logger.debug(f"Wait for network idle failed/timed out: {e}")

                post_url = await runtime.evaluate("window.location.href")
                post_fingerprint = await recovery.dom_fingerprint(runtime)

                if not result.success:
                    result, step, replan_attempts = await recovery.recover_failed_step(
                        step=step,
                        result=result,
                        pre_url=pre_url,
                        post_url=post_url,
                        pre_fingerprint=pre_fingerprint,
                        post_fingerprint=post_fingerprint,
                        page=page,
                        input_domain=input_domain,
                        runtime=runtime,
                        llm=llm,
                        replan_attempts=replan_attempts_by_index.get(step_idx, 0),
                        max_replans_per_step=self._max_replans_per_step,
                        language=self.language,
                    )
                    replan_attempts_by_index[step_idx] = replan_attempts
                    step_list[step_idx] = step
                    self._state.planned_steps = step_list
                    self._save_checkpoint("EXECUTING")

                # Highlight the interacted element before screenshot
                last_selector = extract_selector(result.action_desc)
                if last_selector:
                    await CursorAnimator.highlight_element(runtime, last_selector)

                # Screenshot
                screenshot_b64 = await page.screenshot()

                # Clear highlight after screenshot
                if last_selector:
                    await CursorAnimator.clear_highlight(runtime)

                # Annotate via vision LLM
                language_instruction = (
                    f"Write in {self.language}"
                    if self.language
                    else "Write in the same language as the GOAL, not the UI text visible in the screenshot"
                )
                annotation = await annotation_llm.complete_with_vision(
                    prompt=ANNOTATOR_PROMPT.format(
                        language_instruction=language_instruction,
                        goal=self.goal,
                        step=step,
                    ),
                    image_b64=screenshot_b64,
                )

                doc.add_step(
                    step_number=i,
                    title=step,
                    screenshot_b64=screenshot_b64,
                    annotation=annotation,
                    action_description=self._format_step_action_details(result),
                    status=result.status or "completed",
                    status_reason=result.failure_reason,
                )

                # Store baseline data for self-healing doc tests
                doc.steps[-1]._baseline_dom_hash = post_fingerprint
                doc.steps[-1]._baseline_url = post_url

                self._state.executed_results.append(asdict(result))
                results.append(result)

                # Track step metrics
                step_latency = (time.monotonic() - step_start) * 1000
                metrics = get_metrics()
                if metrics:
                    metrics.increment("step.executed")
                    metrics.record("step.latency_ms", step_latency)
                    if result.success:
                        metrics.increment("step.succeeded")
                    else:
                        metrics.increment("step.failed")
                    if result.attempts > 1:
                        metrics.increment("step.retries", result.attempts - 1)

                step_idx += 1
                self._state.current_step_index = step_idx
                self._save_checkpoint("EXECUTING")

                i += 1
                self._cursor.moving = True

            except (
                websockets.exceptions.ConnectionClosed,
                WebSocketDisconnected,
            ) as exc:
                # READY-AI-T-3: BOTH disconnect subtypes route through
                # the same coordinator — it waits for the connection's
                # own reconnect+reattach first, and only then falls
                # back to a bounded full respawn.
                metrics = get_metrics()
                if metrics:
                    metrics.increment("recovery.crash")
                await self._recover_session_after_disconnect(
                    exc, llm, step_idx
                )
                page = self._session.page
                input_domain = self._session.input_domain
                runtime = self._session.runtime

        return results

    async def _critic_loop(
        self,
        markdown: str,
        llm: LLMClient,
        annotation_llm: LLMClient,
        doc: DocRenderer,
        step_results: list[executor.StepResult],
    ) -> None:
        """
        Critic review loop with re-execution of missing steps.

        If the critic identifies missing steps, they are sent back to the
        Planner for a sub-plan, then executed and appended to the documentation.
        """
        for round_num in range(self.max_critic_rounds):
            logger.info(f"═══ Critic review (round {round_num + 1})")
            feedback = await critic.review(markdown, self.goal, llm)

            logger.info(f"    Score: {feedback.score}/10")
            logger.info(f"    Complete: {feedback.is_complete}")

            if feedback.is_complete and feedback.score >= 7:
                logger.info("    ✓ Documentation approved by critic")
                return

            if feedback.suggestions:
                logger.info(f"    Suggestions: {feedback.suggestions}")

            if feedback.missing_steps:
                logger.info(f"    ⟳ Re-executing {len(feedback.missing_steps)} missing steps")
                new_results = await self._reexecute_missing_steps(
                    feedback.missing_steps, llm, annotation_llm, doc,
                )
                step_results.extend(new_results)
                markdown = doc.render()
                self._cursor.moving = True
            else:
                doc.add_critic_notes(feedback.feedback, feedback.suggestions)
                markdown = doc.render()
                self._cursor.moving = True

    async def _reexecute_missing_steps(
        self,
        missing_steps: list[str],
        llm: LLMClient,
        annotation_llm: LLMClient,
        doc: DocRenderer,
    ) -> list[executor.StepResult]:
        """
        Re-plan and execute missing steps identified by the critic.

        Returns:
            List of StepResult from the re-executed steps
        """
        page = self._session.page
        runtime = self._session.runtime

        dom_html = await page.get_dom_html(max_length=4000)
        elements = await runtime.get_interactive_elements()

        missing_context = "\n".join(f"- {s}" for s in missing_steps)
        supplement_prompt = (
            f"MISSING STEPS TO COVER:\n{missing_context}\n\n"
            f"INTERACTIVE ELEMENTS:\n{elements}\n\n"
            f"PAGE HTML (truncated):\n{dom_html[:3000]}\n\n"
            f"Generate the numbered steps to cover these gaps:"
        )
        if self.language:
            supplement_prompt += f"\nIMPORTANT: Write all output in {self.language}."

        messages = [
            {"role": "system", "content": PLANNER_SUPPLEMENT_SYSTEM},
            {"role": "user", "content": supplement_prompt},
        ]

        response = await llm.complete(messages, role="planner")
        new_steps = planner._parse_steps(response)

        if not new_steps:
            logger.warning("    Planner returned no supplement steps")
            return []

        logger.info(f"    Planner generated {len(new_steps)} supplement steps")

        next_num = len(doc.steps) + 1
        return await self._execute_steps(
            new_steps, llm, annotation_llm, doc,
            start_number=next_num,
            # READY-AI-T-3/Q3: a sub-plan always starts at ITS OWN
            # first step. Seeding from `state.current_step_index`
            # (the MAIN-plan cursor) would skip the first missing
            # step once that cursor is nonzero.
            start_index=0,
        )
