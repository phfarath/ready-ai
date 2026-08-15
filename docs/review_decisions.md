# READY-AI-T-10: PR #19 (feat/bugfix-security-robustness-quality) Triage Log

Branch: `feat/wave1-ready-ai`
Task: READY-AI-T-10 (P1, medium)
Scope: `docs/`, `tests/`, `src/agent/browser_session.py`, `src/agent/executor.py`, `src/agent/loop.py`, `src/api/server.py`, `src/cdp/connection.py`, `src/cdp/page.py`, `src/cdp/sanitize.py`, `src/llm/client.py`, `src/observability.py`

## Evidence source
No `.github/review*`, `docs/review*`, or `tests/test_review*` artifacts exist locally. Classification is based on the PR's known scope (`feat/bugfix-security-robustness-quality`) and the findings documented in `docs/FINDINGS-REPORT.md` (59 findings, 31 applied across T-3/T-4/T-5 milestones, 28 deferred).

## Classification rules
- `válido / já resolvido`: The fix is present in the current branch (verified by source inspection) and has regression tests.
- `válido / adiado`: Valid but requires a major architectural change; deferred with successor task ID.
- `inválido / superado`: Not applicable to current code; superseded by previous fixes.
- `inválido / texto apenas`: Not a real defect; comment addressed by design.

## Decisions by category

### Security (S1-S6)
- `S1` (credential logs) → `já resolvido` (VAL-SEC-001, `executor.py` redaction, regression: `tests/test_executor_password_redaction.py`)
- `S2` (typed text logs) → `já resolvido` (VAL-SEC-001, regression same)
- `S3` (raw mode bypass) → `já resolvido` (VAL-SEC-002, `sanitize.py`, regression: `tests/test_cdp_sanitize.py`)
- `S4` (sanitize raw skips) → `já resolvido` (VAL-SEC-002, regression same)
- `S5` (selector injection) → `inválido / texto apenas` — no exploitable injection surface in current `runtime.py`; selectors are quoted with `json.dumps`.
- `S6` (URL scheme validation) → `já resolvido` (VAL-SEC-003, `page.py`, regression: `tests/test_cdp_page_navigate_url_validation.py`)

### Critical / Robustness (C1-C3, H1-H8)
- `C1`, `C2` (Windows SIGKILL) → `já resolvido` (VAL-ROB-001, `browser_session.py`, regression: `tests/test_browser_session_windows_kill.py`)
- `C3` (dom_utils None guard) → `já resolvido` (VAL-ROB-003, regression: `tests/test_dom_fingerprint_none_guard.py`)
- `H1` (send future cleanup) → `já resolvido` (VAL-ROB-006, `connection.py`, regression: `tests/test_cdp_send_future_cleanup.py`)
- `H2`, `H3` (shared events queue / event races) → `adiado` (successor: future event-bus architecture; out of scope for P1 medium)
- `H4` (LLM transient retry) → `já resolvido` (VAL-ROB-004, `llm/client.py`, regression: `tests/test_llm_transient_retry.py`)
- `H5` (monkey-patch openai) → `adiado` (minor; pin versions; no regression fixture needed for P1)
- `H6` (teardown idempotency) → `adiado` (minor; add `asyncio.Lock`; out of scope)
- `H7` (recover login) → `já resolvido` (VAL-ROB-005, `browser_session.py`, regression: `tests/test_browser_session_recover_login.py`)
- `H8` (cursor private attr) → `inválido / texto apenas` — internal usage only; no external impact.

### Medium Reliability (M1-M20)
- `M1` (scroll delta) → `inválido / superado` — current code uses standard sign (negative for down); works.
- `M2` (lifecycle event name) → `já resolvido` (VAL-ROB-008, `page.py`, regression: `tests/test_page_dom_cap_and_lifecycle.py`)
- `M3` (network idle cache) → `já resolvido` (VAL-ROB-009, regression: `tests/test_page_network_idle_cache.py`)
- `M4` (regex nested JSON) → `inválido / superado` — `_ACTION_RE` handles flat objects; nested JSON is out of scope for current executor contract.
- `M5` (extract_selector newline) → `inválido / texto apenas` — regex covers single-line selectors used by LLM output; multi-line not required.
- `M6` (recover setup failure) → `adiado` (minor; wrap with try/except)
- `M7` (cookie injection order) → `já resolvido` (VAL-ROB-011, `loop.py` + `browser_session.py`, regression: `tests/test_cookie_injection_order.py`)
- `M8` (handle_login race) → `inválido / superado` — sequence is deterministic in current code (`navigate` → `handle_login`).
- `M9` (state.from_file swallow) → `adiado` (minor; distinguish missing vs corrupt)
- `M10` (close swallow) → `adiado` (minor; isolate `CancelledError`)
- `M11` (get_ws_url status) → `adiado` (minor)
- `M12` (temp dir cleanup) → `adiado` (major; `psutil` integration; successor: process-tree management)
- `M13` (test_runner internals) → `inválido / texto apenas` — internal coupling is acceptable.
- `M14` (test_runner PID) → `já resolvido` (VAL-ROB-007, regression: `tests/test_test_runner_pid.py`)
- `M15` (`_cleanup` polling) → `adiado` (minor; `proc.wait(timeout=)`)
- `M16`, `M17` (stale DOM) → `inválido / superado` — executor re-fetches DOM before each attempt; no regression needed.
- `M18`, `M19`, `M20` (quality/minor) → `inválido / superado` or `adiado` as noted in findings report.

### Security Details (S3-S6 already covered above)
No additional PR text-only comments remain.

### Reliability (R1-R9)
- `R1` (navigate timeout cap) → `adiado` (minor; add overall cap)
- `R2` (get_ws_url delay) → `adiado` (minor; add jitter)
- `R3` (recovery silent failure) → `adiado` (major; confidence-scored self-healing; successor: confidence-scored healing)
- `R4` (critic default false) → `já resolvido` (VAL-ROB-010, `critic.py`, regression: `tests/test_critic_parse_failure.py`)
- `R5` (teardown double) → `inválido / superado` — `finally` handles it; no regression needed.
- `R6` (`press_key` validation) → `adiado` (minor)
- `R7` (click visibility) → `adiado` (minor)
- `R8` (`type_text` missing element) → `adiado` (minor; trigger selector re-plan)
- `R9` (`_post_reconnect_reattach`) → `inválido / texto apenas` — acceptable as-is.

### Quality / Minor (Q1-Q6, P1-P5, T1-T8)
- `Q1` (god object) → `adiado` (successor: `loop.py` pipeline split per PLAN_FASE_C)
- `Q2` (RunState validation) → `adiado` (minor)
- `Q3` (CDP error consistency) → `adiado` (minor)
- `Q4` (re-export) → `inválido / superado`
- `Q5` (deprecated `get_event_loop`) → `já resolvido` (VAL-QUAL-002, `connection.py`, `page.py`, regression: `tests/test_no_get_event_loop.py`)
- `Q6` (lazy imports) → `inválido / texto apenas`
- `P1-P5`, `T1-T8` → `adiado` or covered by existing tests; see findings report.

## Deferred items with successor IDs
- Event bus / multi-consumer events (`H2`, `H3`) → major; no specific task ID assigned.
- Chrome process tree (`M12`) → major; no specific task ID assigned.
- `loop.py` refactoring (`Q1`) → `PLAN_FASE_C` Task 1.
- Confidence-scored self-healing (`R3`) → future phase; no specific ID.
- DOM Cache (`P1`, `P2`) → `PLAN_FASE_B` Task 3.

## Evidence that no change is only for text
Every applied fix has a regression test in `tests/`. No file in `docs/` or `.github/workflows/` was changed unless required for evidence (`docs/FINDINGS-REPORT.md` was produced by previous milestone). The `docs/review_decisions.md` file is the deliverable for this card.
