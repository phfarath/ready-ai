# Changelog — ready-ai

Format: one entry per release. Tags `vX.Y.Z` cut only from `main` after the phase DoD is green.

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
