# Next Steps — Active Delivery Plan

**Last updated:** 2026-03-06
**Current phase:** contributor polish + CLI hardening

## Completed Baseline

Already delivered in the repository:

- Planner -> Executor -> Critic loop
- DOM-based post-action verification
- retry loop with fallback strategies
- critic-driven gap re-execution
- cookies and credential-based auth
- `--title`, `--language`, `--annotation-model`
- checkpoint persistence and resume support
- FastAPI scaffold with background runs and ZIP output download
- API tests rewritten to use ASGI in-process requests
- local `pytest -q` baseline restored

## Delivery 1 — Documentation Alignment

Status: complete

Goals:

- align `README.md` with the real `run` and `api` commands
- update `PRD.md` to reflect current product phase
- replace historical changelog planning with an active roadmap
- refresh contributor instructions in `AI_INSTRUCTIONS.md`
- sync `FEATURES.md` with API/checkpoint capabilities

## Delivery 2 — API and Test Stabilization

Status: complete

Priority items:

- fix dependency incompatibilities in the API test stack
- make installation paths for CLI, API, and tests consistent
- restore a green `pytest` baseline
- add or fix API tests for create, poll, resume, and output download
- document the supported runtime/dependency matrix

Acceptance criteria:

- `pytest -q` runs cleanly in a fresh environment
- API examples in `README.md` work as documented
- API server startup requirements are explicit and reproducible

## Delivery 3 — GitHub Public Repo Setup

Status: complete

Priority items:

- set GitHub repository description and topics
- make the README first screen explicitly say "open-source core"
- decide final repository visibility after the about box is aligned
- keep only the support surfaces we will actually maintain
- avoid placeholder homepage URLs or launch claims

Acceptance criteria:

- the GitHub About box explains the open-source-core scope in one line
- discovery topics match browser automation and documentation generation
- a first-time visitor understands both what is included and what is not
- the repository can be switched to public without messaging ambiguity

## Delivery 4 — Remaining CLI Hardening

Status: next

Priority items:

- better recovery for `[FAILED]` steps
- `--config` YAML/TOML support
- dry-run / plan-only mode
- stronger readiness/network-idle heuristics

Acceptance criteria:

- repeated runs are reproducible without long flag lists
- planning can be inspected without opening a real browser flow
- failed steps degrade gracefully instead of polluting docs

## Delivery 5 — Coverage for Real SaaS Flows

Status: later

Priority items:

- OAuth / SSO
- MFA / TOTP
- multi-tab flows
- cross-origin iframe strategy

Acceptance criteria:

- the engine can handle common auth patterns found in modern SaaS apps
- documentation runs are not blocked by basic multi-context navigation

## Delivery 6 — Product Surface Expansion

Status: later

After the baseline is stable:

- webhook callback support
- Docker packaging
- web UI and dashboard
- scheduled runs
- destination integrations

## Notes

- Web UI should stay behind CLI hardening; otherwise the project compounds instability.
- The roadmap now treats the API as existing but immature, not as a future phase.
- Public launch messaging should stay narrower than the long-term product vision.
- Local contributor setup should stay centered on `pip install -e ".[dev]"`.
