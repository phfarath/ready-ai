# Product Requirements Document — browser-auto

**Version:** 0.2
**Last updated:** 2026-03-06
**Status:** Living document — reflects current repository state

---

## 1. Executive Summary

browser-auto is an agentic browser automation engine that generates screenshot-rich user documentation for SaaS applications. Given a goal and a URL, it plans a documentation run, executes it step-by-step through raw Chrome DevTools Protocol, critiques the result, and produces a portable Markdown document with screenshots.

The product is no longer "CLI only". The repository now contains:

- a working local CLI
- a checkpointing/resume layer
- an initial FastAPI service for background runs and output download

The current product phase is **CLI hardening + API stabilization**. The longer-term vision remains a SaaS platform where teams trigger runs, manage outputs, schedule re-runs, and publish to external destinations.

---

## 2. Problem

Writing user documentation for SaaS products is:

- **Manual and slow** — a single feature walkthrough can take hours to capture, annotate, and format.
- **Always out of date** — every UI change requires a full re-shoot.
- **Inconsistent** — different writers produce different quality; annotations are subjective.
- **Expensive** — technical writers or PMs spend significant time on documentation that is not core product work.

Existing solutions either require a human to operate a screen recorder or depend on brittle scripted macros. browser-auto is designed to use LLM planning, adaptive execution, and critique in one automated loop.

---

## 3. Solution & Product Vision

browser-auto closes this gap with a three-agent pipeline:

1. **Planner** — given a goal and the current page DOM, generates a step-by-step plan.
2. **Executor** — converts each step into a CDP action, verifies success, retries on failure, and annotates screenshots with a vision LLM.
3. **Critic** — reviews the generated docs, scores quality, identifies gaps, and triggers re-execution of missing steps.

The loop is self-healing:

- URL drift can trigger replanning
- failed steps can retry with different strategies
- the critic can request supplemental steps
- interrupted runs can resume from checkpoint

**Vision:** a documentation platform where any developer, PM, or technical writer can type a goal, point at a URL, and receive publishable docs in minutes, with scheduling, destination integrations, and history over time.

---

## 4. Target Users

| Persona | Pain | How browser-auto helps |
|---------|------|------------------------|
| **Solo developer / indie hacker** | No time or budget for a technical writer | Run the CLI after shipping and get docs immediately |
| **Startup product team** | UI changes faster than docs can keep up | Re-run docs through the API or future schedules |
| **Technical writer** | Spends hours on screenshots and step-by-step capture | Generate a strong first draft automatically |
| **QA / DevOps** | Needs release documentation and evidence | Use background runs and exported artifacts |

---

## 5. Current Product State

### Local CLI

- `main.py run` starts a documentation run locally
- supports `--title`, `--language`, `--annotation-model`, cookies, credentials, and headless mode
- writes Markdown, screenshots, summary, and checkpoint state to disk

### Agentic loop

- Planner → Executor → Critic orchestration is implemented
- post-action verification uses DOM fingerprint + URL
- executor retries failed steps with fallback strategies
- critic can request missing steps and re-execute them
- URL drift and SPA drift are partially handled through replanning

### Browser automation

- raw CDP over WebSocket
- automatic Chrome launch
- shadow DOM traversal
- same-origin iframe traversal
- element highlighting before screenshot
- network idle waiting based on CDP network events

### Authentication

- session cookie injection from JSON file
- auto-login for common username/password forms
- secure JS string escaping with `json.dumps()`

### Output

- portable Markdown + screenshots directory
- auto-generated table of contents
- collapsible technical details per step
- title separate from goal
- multilingual renderer labels and prompt instructions

### API scaffold

- `main.py api` starts a FastAPI server
- `POST /runs` creates a background run
- `GET /runs/{run_id}` returns status
- `GET /runs/{run_id}/output` returns a ZIP archive
- checkpoints allow resume when the same `run_id` is reused

### Known product gaps

- API dependency/test stack is not yet stable
- no webhook callback yet
- no Docker packaging yet
- no Web UI yet
- complex auth flows and multi-context browsing are still incomplete

---

## 6. Architecture Overview

```
CLI / API entry points
  ├── main.py run
  └── main.py api
        ├── FastAPI endpoints (src/api/server.py)
        ├── RunManager (src/api/manager.py)
        └── AgenticLoop (src/agent/loop.py)
              ├── checkpoint state (src/agent/state.py)
              ├── Planner (src/agent/planner.py)
              ├── Executor (src/agent/executor.py)
              ├── Critic (src/agent/critic.py)
              └── DocRenderer (src/docs/renderer.py)

CDP Layer (src/cdp/)
  ├── CDPConnection — raw WebSocket JSON-RPC
  ├── PageDomain — navigation, screenshot, DOM, network idle waiting
  ├── InputDomain — click, type, scroll
  └── RuntimeDomain — JS eval, interactive elements, shadow DOM, iframes

LLM Layer (src/llm/)
  ├── LLMClient — async LiteLLM wrapper, vision, retry
  └── prompts.py — planner, executor, critic, supplement, retry, annotation prompts
```

**Key invariant:** no browser automation framework dependency. All browser control flows through raw CDP messages in `src/cdp/`.

---

## 7. Roadmap

### Phase 1 — CLI Stabilization (completed or mostly completed)

The following items are already implemented in the repository:

- `--language`
- checkpoint save/resume
- annotation model separation
- retry loop with fallback strategies
- critic re-execution of missing steps
- authentication via cookies and basic credentials

### Phase 1.5 — Current Focus: API Stabilization + Remaining CLI Gaps

This is the active delivery phase.

| Item | Priority | Notes |
|------|----------|-------|
| Stabilize API dependency and test stack | High | Make API usage and tests reproducible, compatible, and trustworthy |
| Better failed-step recovery | High | Reduce `[FAILED]` output leakage and improve retry/skip strategy |
| `--config` YAML/TOML file | Medium | Reproducible runs and easier automation |
| Dry-run / plan-only mode | Medium | Print plan without executing browser actions |
| Harden network-idle and readiness heuristics | Medium | Improve determinism for dynamic SaaS apps |
| Webhook callback | Low | Useful once API contract is stable |
| Docker packaging | Low | Needed for external consumers and self-hosting |

### Phase 2 — Coverage for Real SaaS Flows

- OAuth / SSO handling
- MFA / TOTP handling
- multi-tab support
- cross-origin iframe strategy
- stronger regression coverage on representative SaaS apps

### Phase 3 — Web UI & Dashboard

- Trigger runs from a browser UI
- Live progress stream
- Rendered output viewer and screenshot gallery
- Run history and change diffing
- User authentication and API keys

### Phase 4 — Scheduled Runs & Integrations

- Schedule re-runs on cron or deploy webhook
- Destination integrations: Confluence, Notion, GitHub Pages, PDF
- Slack or email notifications
- Custom output templates

### Phase 5 — Hosted SaaS Platform

- Multi-tenant hosted service
- Subscription tiers
- Team collaboration
- Usage analytics and documentation coverage tracking

---

## 8. Success Metrics

| Metric | Target |
|--------|--------|
| CLI time-to-first-docs | < 5 min for a 10-step flow |
| Critic score on generated docs | >= 7/10 on first pass for standard SaaS flows |
| Failed steps per run | < 10% of total steps |
| Resume reliability | Interrupted runs can continue without redoing completed steps |
| API reliability | Background runs and output download succeed consistently |
| Annotation language consistency | 100% match with requested or inferred output language |

---

## 9. Out of Current Scope

These are not part of the current delivery phase, even if they may become future work:

- video recording
- generating test scripts or code
- polished multi-tenant SaaS hosting
- collaboration workflows such as comments and approvals

Items intentionally deferred, but still plausible later:

- cross-origin iframe support
- multi-tab flows
- MFA / TOTP

