# CDP Resilience — Implementation Tracker

Last updated: 2026-06-12 (post P1-2 merge, commit `70f72be`).

This document tracks the work done on the `src/cdp/` layer against the
resilience roadmap. The roadmap itself is fragmented across
`ROADMAP.md` (release-side) and the original analysis report
saved to `.factory/artifacts/cdp-analysis-report.md`. This file is
the source of truth for **what shipped, what is still pending, and
which PRs to look at**.

## Status legend

- merged — landed on `main`.
- in-review — PR open.
- pending — planned, not started.
- blocked — needs design discussion.

## Completed

| Phase | What | PR | Tests added | Total tests after |
| --- | --- | --- | --- | --- |
| QW #1 | AX tree + dual-mode PII (sanitize helper) | #14 | 14 | 227 |
| QW #2 | Chrome launch flags + env vars | #14 | 29 | 209 (parallel batch) |
| QW #3 | `CDPConnection.send` observability (Span + counter) | #14 | 5 | 209 |
| QW #4 | `dom_fingerprint` dedup + error counter | #14 | 4 | 209 |
| QW #5 | `cursor.py` throttle with backoff | #14 | 2 | 209 |
| QW #6 | `wait_for_network_idle` short-lived cache | #14 | 4 | 209 |
| QW #7 | `READY_AI_DOM_MAX_CHARS` cap on `get_dom_html` | #14 | 12 | 209 |
| QW #8 | `Page.setLifecycleEventsEnabled` + lifecycle-event path | #14 | (same) | 209 |
| P0-1 | `ConnectionState` FSM + `WebSocketDisconnected` | #15 | 8 | 235 |
| P0-1 | Native `ping`/`ping_timeout` + drain | #15 | 14 | 249 |
| P0-1 | Reconnect loop with backoff + hybrid re-attach | #15 | 7 | 256 |
| P0-1 | Circuit breaker with sliding window | #15 | 9 | 265 |
| P0-1 | `BrowserSession` integration (hooks, structured logs) | #15 | 9 | 274 |
| P0-1 | Integration tests + docs | #15 | 5 | 279 |
| P1-2 | Sanitize module (pure-functional) | #17 | 67 | 346 |
| P1-2 | HTML sanitization in `get_dom_html` | #17 | 8 | 354 |
| P1-2 | Interactive element sanitization in `get_interactive_elements` | #17 | 11 | 365 |
| P1-2 | Docs (`CI-CD.md` section + README pointer) | #17 | 0 | 365 |

**Total tests:** 365 passing (from a baseline of ~120 before QW #1).

**Lint:** `ruff check src/ tests/ main.py` clean.

## Pending

### P0-4 — Resilience cross-cutting (low risk, 2-3 days)

- Healthcheck endpoint `GET /health/cdp` returning FSM state, CB state, last disconnect timestamp, current target_id.
- Surface `cdp.*` metrics through the existing `/metrics` API endpoint.
- Per-operation timeouts (currently a single 30s default; SaaS-heavy sites need finer-grained budgets).
- Graceful degraded mode for `BrowserSession.recover()` when Chrome cannot be relaunched (e.g. CI sandbox with no Chrome installed).

### P1-4 — Multi-tab flows (medium risk, 5-7 days)

- `BrowserDomain.list_targets()` + `Target.attachToTarget` helpers.
- `PageDomain` instances bound to a specific `sessionId` (the existing code already passes `session_id` through, but the higher-level helpers don't expose it).
- `TabManager` with explicit open/close/switch semantics.
- New `RuntimeDomain.get_interactive_elements` for a tab other than the focused one.
- LLM planner prompt: how to express "switch to tab 2" as a step.
- Tests: tab navigation, tab close, focus blur, same-origin vs cross-origin policy.
- Docs: "Multi-tab flows" section in `docs/CI-CD.md`.

### P1-5 — Cross-origin iframe coverage (medium risk, 3-4 days)

- The current `get_interactive_elements` skips cross-origin iframes silently. Make this opt-in via a per-flow flag in `.ready-ai.yaml`.
- Frame tree traversal is already implemented for same-origin iframes (`RuntimeDomain.get_interactive_elements`, the `traverseRoot` function) — extend it.

### P2-1 — Structured logs across CDP (low risk, 1-2 days)

- Every `cdp.send()` should emit a structured log line at DEBUG level with method, duration, and target/session.
- Already partially in place from QW #3. Need to extend to the `recv` side and to the FSM transitions.

### P2-2 — Per-method latency histograms (low risk, 1 day)

- `cdp.latency_ms{method=...}` histogram already exists. Extend to per-method breakdown and persist to a SQLite file (or roll out via the existing `Metrics` flush path).

### P2-3 — `cdp` OpenTelemetry exporter (low risk, 1-2 days)

- Optional: emit spans to OTLP. The current `Span` class is in-memory only.

### P2-4 — Selector health score (medium risk, 3-4 days)

- Track which selectors break most often across runs; surface in the existing `selector_health` API (if implemented) or in `GET /runs/{run_id}/selectors`.

### P2-5 — Heuristic-driven connection pooling (medium risk, 2-3 days)

- Open multiple CDP pages on the same Chrome instance, share a connection when possible.

### P2-6 — Per-target sandbox (low risk, 1 day)

- Allow flows to declare which Chrome profile / data-dir they need.

## Branch conventions

- `feat/cdp-quick-wins` (merged) — the 8 quick wins batch.
- `feat/cdp-p0-1-reconnect` (merged) — phase 1 reconnect/CB.
- `feat/cdp-p1-2-sanitize` (merged) — LGPD-safe DOM sanitization.
- Future branches: `feat/cdp-p1-4-multitab`, etc.

Each branch is rebased onto `main` after the previous phase lands. No
squash from local — each commit is preserved with its full body for
the audit trail.

## How to extend this file

When a new phase lands, append a row to the Completed table with the
PR number, test counts, and the SHA of the merge commit. When a new
phase is approved, add a sub-section under Pending with the planned
work, the test count target, and the link to the design discussion
(in the assistant conversation or in a design doc under `docs/`).
