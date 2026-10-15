# Changelog — ready-ai

Format: one entry per release. Tags `vX.Y.Z` cut only from `main` after the phase DoD is green.

## Versioning policy

`ready-ai` follows SemVer while in `0.x`: **minor** releases may add
surface and adjust behavior documented in the release entry; **patch**
releases are fixes only. Within the public SDK surface
(`ready_ai` package: `ReadyAI`, `Flow*`/`Run*` models, `RunEvent`,
the exception taxonomy), a breaking change — removal, signature change
or semantic change of a documented name — first appears as a
deprecation: the old name keeps working for at least one minor release
and is announced in this changelog. Internal modules (`src.*`) are not
covered by this policy; consumers must not import them.

## v0.6.1 — 2026-10 — SDK + healing review follow-ups (patch)

- Checkpoint→flow binding (`READY-AI-T-SDK-FOLLOWUPS/1`): sha256 of the
  flow's behavioral identity persisted in `RunState` on pause, verified
  on resume before Chrome launches (engine) and at the SDK boundary
  (`ReadyAI.resume`) — a foreign flow can never resume under another
  run's `run_id`. Runtime knobs/credentials excluded from the hash;
  legacy checkpoints skip with a warning.
- `replay_manifest` boundary hardening (2/3): Mapping manifests
  pre-validated at the SDK boundary (`FlowValidationError`, not generic
  `ReadyAIError`); `RecursionError` wrapped on both branches.
- `openai._compat` guarded import (6), new `api` extra + friendly error
  on `ready-ai api` without it (7), honest lazy-import docstrings (8).
- Healing follow-ups (`READY-AI-T-US5-FOLLOWUPS`, TDD): `suspected-only`
  publish reason, DOM-baseline provenance warning, diff triptych for
  `DRIFT_SUSPECTED`, `min_channels` upper clamp, verified selector
  persistence to `docs.md`.
- Fixes only, no public-surface change (per the versioning policy
  above). PRs #51, #52; CI green (lint, strict mypy 3.10–3.12, pytest
  3.10–3.12, e2e, regression, CodeQL).
- Windows console fix: `ready-ai --help` no longer crashes with
  `UnicodeEncodeError` under cp1252 (parser description de-emojified;
  regression test `test_cli_help_encodes_on_windows_console`). Found by
  the v0.6.1 clean-venv wheel smoke test.

## v0.6.0 — 2026-10 — Public async-first SDK + distribution contract (Fase 5)

- Async-first public API: `ReadyAI.run()` / `run_sync()` /
  `start()` + `RunHandle` (ordered `RunEvent` stream, cooperative
  `cancel()`, `done`), and `resume(run_id)` for human checkpoints —
  all behind typed public exceptions (`ReadyAIError` family); no
  `src.*` import is ever needed on the consumer side.
- Distribution contract: wheel ships `ready_ai` + PEP 561 `py.typed`
  and full metadata (readme, MIT license, classifiers, project URLs);
  strict `mypy` gates `ready_ai` + `examples/` on Python 3.10/3.11/3.12;
  CI builds the wheel and installs it into a clean venv (import + CLI +
  minimal SDK smoke) on every PR.
- Versioning policy formalized above; the public surface is now the
  compatibility contract.
- Reviewed across multiple passes before merge (PRs #49, #50); CI
  green (lint, strict mypy 3.10–3.12, pytest 3.10–3.12, e2e,
  regression, CodeQL).
- Known follow-up (tracked in `READY-AI-T-SDK-FOLLOWUPS`): paused
  checkpoints are not yet bound to the originating flow fingerprint.

## v0.5.0 — 2026-10 — Diagnosable failures + measured healer precision (Fase 4)

- Flight recorder: every terminal failure (`run`, `run_flow`, pipeline)
  writes `<run_id>_failure.json` — failed step, URLs (scheme/host/path
  only), sanitized DOM excerpt, screenshot, console/exception/Log rows,
  network events and timings — correlated to the run; bounded by
  `READY_AI_FR_*` caps and retention.
- Sanitized event history at the source: `_history_safe_event` retains
  only routing/outcome fields — never headers, postData, response bodies
  or credentials; `Runtime`/`Log` are now enabled on the primary session
  so console rows reach the bundle.
- Multi-causal healing gate effective on main (US5 port): auto-heal
  requires ≥2 causally distinct channels agreeing; a single channel
  yields `DRIFT_SUSPECTED` and never heals silently; per-step channel
  agreement logged; healing never rewrites step assertions.
- Mutation harness (`tools/mutation_harness`): YAML-declared mutations
  applied over CDP measure TP/FP/FN/TN per regime (single_visual,
  single_dom, gated); scorecard rendered into the HTML report
  (`render_healer_scorecard`); monthly drill documented in
  `docs/MUTATION-DRILL.md`.
- All three PRs (#44/#45/#46) independently reviewed before merge;
  CI green (lint, pytest 3.10–3.12, e2e, regression, CodeQL).
- Known pre-existing (tracked, not gating):
  `test_chrome_kill_reports_terminal_disconnect` kill-timing race
  (`READY-AI-T-E2E-KILL-RACE`); GitGuardian false positive on a test
  fixture in PR #44 history (de-fanged at head; non-required check).

## v0.4.0 — 2026-09 — Zero-token deterministic replay (Fase 3)

- Manifest compile: verified (`passed`) runs compile to versioned
  `*_replay_manifest.json` (actions + asserts + fingerprints + idempotency
  keys); credential flows refused, manifests carry no secrets of their own.
- Zero-LLM replay: `run_flow(allow_llm=False)`, CLI
  `run-flow --emit-manifest/--replay-manifest`, SDK `ReadyAI.replay_manifest()`.
- Drift gate + bounded fallback: per-step fingerprint compared against the
  manifest BEFORE acting (`DRIFT_SUSPECTED`, never silent heal); divergence
  or failed step triggers one trust-live agentic re-run, recorded in the
  `replay` result block (mode, drift, fallback).
- Before/after cost: authoring (LLM) vs replay (zero) from the existing
  observability counters, heal cost tracked separately.
- E2E matrix 23/24 green incl. replay suites; unit replay scope 40/40.
- Known pre-existing (tracked, not gating): chrome-kill disconnect timing on
  cold Chrome (Windows; CI warms up), 1× `LLMClient` patch test, cp1252
  emoji encoding in 2 CLI/docs tests on Windows.

## v0.3.0 — 2026-09 — Precise, safe core (Fase 2)

- Effect policy + confirmation gates: `read/navigate/write` ceilings, `confirm`
  steps with idempotency keys, `pending_confirmation` instead of silent runs.
- Explicit target/session contexts: real pop-up tabs, cross-origin actuation
  without state crossing (`TargetRegistry`, `wait_for_popup`/`switch_tab`/`close_tab`).
- Files + dialogs: allowlisted upload (paths masked), verified download
  (event/name/size/MIME), explicit native-dialog action with nested trigger.
- Human checkpoint: `await_human` pauses before any actuation with an observable
  resume condition; resume by `run_id` never re-executes; persistent profiles
  via `Profile.user_data_dir`; temp profiles always cleaned up (M12).
- Known pre-existing (tracked, not gating): 3.10 `NotRequired` collection,
  1× `LLMClient` patch test.

## v0.2.0 — 2026-09 — Local E2E harness green in CI (Fase 1)

- `tests/e2e/` + `tests/fixtures/e2e_server.py`: 11 real-browser tests (SPA,
  Shadow DOM, redirect, same/cross-origin iframes, popup opener, custom
  dialog, download, Chrome-kill disconnect, truthful sanitized failure).
- New `e2e` CI job (setup-chrome + warmup); unit job ignores `tests/e2e`.
- Engine fix found by the harness: explicit executor success wordings
  (`KNOWN_SUCCESS_PREFIXES`) — declarative `click` can pass `run_flow`.
- Known pre-existing (tracked, not gating): 3.10 `NotRequired` collection,
  1× `LLMClient` patch test.

## Unreleased — engine-first pivot

- `ENGINE-ROADMAP.md`: docs-centric plans frozen; 5 phases with expected outcomes; release train v0.2.0–v0.6.0.
- README rewritten engine-first (60s quickstart, precision benchmark, is/is-not).
- New `docs/ARCHITECTURE.md` mirroring the real modules.
- Cortex sprint `engine-first`: `READY-AI-T-PH0` … `READY-AI-T-PH5`.

## v0.1.0 — 2026-09 — Verifiable CDP workflows (Wave 1)

- `ready_ai` public SDK façade: `ReadyAI.run_flow()`, `Flow`/`FlowStep`/`FlowAction`/`FlowAssertion`/`FlowExtraction`, `EffectPolicy` ceilings, sanitized `RunResult`.
- Engine run-flow mode: declarative flows without screenshots or docs rendering.
- Semantic locators with safe-action fallback (`click_text`) and executor disconnect fix.
- Docs regression gate with strict prerequisites; multi-causal healing gate (visual AND DOM must agree, else `DRIFT_SUSPECTED`).
- Positioning calibrated: no "only tool" claim (Guidewright, Stagehand v3 acknowledged).

## Planned

| Release | Phase | Content |
|---------|-------|---------|
| v0.2.0 | Fase 1 | Local E2E harness green in CI |
| v0.3.0 | Fase 2 | Precise, safe core (policy, targets, uploads, sessions) |
| v0.4.0 | Fase 3 | Zero-token deterministic replay |
| v0.5.0 | Fase 4 | Flight recorder + healer scorecard |
| v0.6.0 | Fase 5 | Distributable public SDK |
