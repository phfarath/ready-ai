# Architecture — ready-ai engine

The product is the automation engine. Documentation generation is one consumer of it.
Read with the code open: every section names the module that owns the behavior.

```
Flow (ready_ai.models) ──► ReadyAI.run_flow ──► AgenticLoop.run_flow ──► executor ──► CDP ──► Chrome
        ▲ validates                    │ translates to              │ asserts + extracts
        │ policy/url/timeouts          │ src.api.models.FlowSpec    ▼
        └────────────────────────── RunResult (sanitized) ◄── structured step outcomes
```

## 1. Public boundary — `ready_ai/`

The only import surface consumers need. `src.*` is an implementation detail.

- `ready_ai/models.py` — serializable contract, `SCHEMA_VERSION = 1`, lenient parsing
  (`extra="ignore"`, newer `version` tolerated). `EffectPolicy`: `observe` (only
  `observe`/`wait`) < `navigate` (+ navigation/scroll) < `interactive` (all actions);
  violations fail at construction. `Flow` requires http(s) URLs without embedded
  credentials; `BrowserOptions.profile` is a validated *reference* (no `..`, no secrets);
  `RunResult`/`RunStep` carry per-step status, attempts, masked actions and only
  artifacts that exist inside the output dir.
- `ready_ai/client.py` — `ReadyAI` façade: profile allowlist registry (`None`, path
  string or `Profile`), `validate_config()` pre-flight, `run_flow()` (async) with a
  whole-run `timeout_s` budget raising `RunTimeoutError`. Translates public models onto
  the engine's `FlowSpec` (`src/api/models.py`); credentials only ever travel as
  resolved registry references.

## 2. CDP layer — `src/cdp/`

Raw Chrome DevTools Protocol over a single WebSocket. No WebDriver, no Node relay.

- `browser.py` — `launch_chrome()` (temp profile per run) and `get_ws_url()` with
  startup retry against `/json/version`.
- `connection.py` — `CDPConnection.send()` with `_pending` futures, a shared `_events`
  queue with `wait_for_event()`, auto-reconnect with backoff (`READY_AI_CDP_AUTORECONNECT`,
  circuit breaker exposes `is_disconnected`). Known limit: the event queue is single
  shared — concurrent waiters can mis-deliver; fan-out is Fase 2 work.
- `page.py` — `navigate()`, `wait_for_navigation_settled()`, `wait_for_network_idle()`,
  `wait_for_selector()` (polling), dialog handling.
- `runtime.py` — `evaluate()`, `get_interactive_elements()` (pierces shadow DOM),
  `find_element_by_text()`, `get_state_fingerprint()` (passwords redacted).
- `input.py` — `click()` (box-model center dispatch), `type_text()` (React-compatible
  native setter), key press. No visibility pre-check on first click — retry path
  scrolls into view; pre-check is Fase 2 work.
- `locator.py` — semantic locators (role/label/test-id first) with safe-action fallback.
- `sanitize.py` — LGPD-safe snapshots by default: sensitive fields always redacted,
  long values truncated. `READY_AI_RAW_DOM=true` is debug-only and bypasses cosmetic
  passes (sensitive redaction must still apply — see audit S3/S4).
- `accessibility.py`, `connection_state.py`, `exceptions.py` — AX tree, reconnect
  state machine, typed CDP errors (`WebSocketDisconnected` vs `RuntimeError`).

## 3. Agent — `src/agent/`

- `loop.py` — `AgenticLoop`: setup → plan → execute → critique → render, with `RunState`
  checkpoints (`state.py`) resumable by `run_id`, crash recovery budget (`MAX_CRASHES`
  via `browser_session.recover()`), and **run-flow mode** (`run_flow()`) executing a
  declarative `FlowSpec` with no screenshots and no docs rendering. ~590 lines:
  the god-object split is tracked for later, not for the pivot.
- `planner.py` — LLM step planning from sanitized DOM (`_parse_steps`).
- `executor.py` — `execute_step()` with retry budget: `click`, `click_text` (safe
  fallback when the selector misses), `type` (text masked in reports), `press_key`,
  `navigate`, `scroll`, `scroll_to`, `wait`, `observe`, tab actions
  (`wait_for_popup`/`switch_tab`/`close_tab`), `upload` (allowlist, paths masked),
  `download` (event/name/size/MIME verified), `dialog` (explicit, nested trigger);
  download expectations evaluated per step. Post-action verification turns visual
  change into asserted outcome.
- `browser_session.py` — Chrome lifecycle (`setup`/`teardown`/`recover`), cookie
  injection, credential auto-login for simple forms, persistent `profile_dir`
  (never deleted) with session-owned temp profiles (always cleaned up, M12);
  recovery re-injects cookies and re-attempts credential login when available.
  Remaining limit: no `psutil` process-tree kill on Windows zombie trees.
- `recovery.py` — `recover_failed_step()`, `recover_locally()`, SPA-drift replan.
- `critic.py` — completeness review with re-execution rounds (`--max-critic-rounds`).
- `dom_utils.py` — `dom_fingerprint()` for cheap change detection (feeds the Fase 3
  replay fallback and the US5 healing gate).
- `cursor.py` — thinking-cursor overlay + `extract_selector()` for failure highlights.
- `test_runner.py` — `DocTestRunner`: the **docs consumer** — replays `docs.md` steps,
  diffs screenshots, heals behind the multi-causal gate. Engine stays usable without it.

## 4. LLM — `src/llm/`

- `client.py` — `LLMClient` over LiteLLM (text + single/multi-image vision). Retry covers
  `RateLimitError` with backoff; broader transient retry + circuit breaker is Fase B
  work, slotted after the pivot harness proves the need.
- `prompts.py` — planner/critic/vision prompts. `llm/` never sees raw secrets: it only
  receives sanitized snapshots from `src/cdp/sanitize.py`.

## 5. Serving — `src/api/` + `main.py`

- `server.py` / `manager.py` — `POST /runs`, `GET /runs/{id}`, output ZIP,
  `POST /webhooks/deploy` (multi-flow on deploy), `POST /batches` + `GET /batches/{id}`;
  `RunManager` with a CDP port pool. Rate limit is in-memory (single worker);
  distributed limiting and API auth are post-pivot hardening, not pivot blockers.
- `main.py` — CLI: `run` (agent), `test` (docs regression + `--auto-heal`), `batch`
  (YAML/TOML multi-flow), `api`, `export` (markdown/html/llms.txt…).
- `observability.py` — in-memory `Metrics` + `RunContext` spans (`run_summary()`).
  Prometheus export is slotted post-harness.
- `notify.py`, `history.py`, `versioning.py` (manifests, `manifest.json` per run),
  `mcp_server.py` — notifiers, run history, versioned doc sets, MCP exposure.

## 6. Docs consumer — `src/docs/`

`renderer`/`parser` (multilingual steps) → `visual_diff` + `semantic_diff` →
`auto_healer` (**multi-causal gate**: heal only when visual drift AND DOM change agree,
else `DRIFT_SUSPECTED`) → `healing_publisher` (reviewable PR body) → `report_html`
(standalone report) / `export` / `text_diff` (`changelog.md` + `diff.json`) /
`terminal_output`. Frozen as example surface: bugfixes yes, features no.

## 7. Effects & safety model

1. Validation is the first gate (`ready_ai.models` + `Flow` policy ceiling).
2. Execution is the second (`executor` post-action verification, idempotency keys per
   step — shipped v0.3.0).
3. Irreversible actions (publish, delete, pay, send) require explicit caller
   confirmation and evidence in the result — no inference from screen text (shipped
   v0.3.0, `READY-AI-T-PH2-PRECISE-CORE`).
4. Checkpoints persist progress, never secrets; MFA/SSO pauses for a human and resumes
   on an observable condition.
