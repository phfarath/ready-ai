# Product Requirements Document — browser-auto

**Version:** 0.1
**Last updated:** 2026-02-23
**Status:** Living document — update when scope or priorities change

---

## 1. Executive Summary

browser-auto is an agentic browser automation engine that generates accurate, screenshot-rich user documentation for SaaS applications — fully automatically. Given a goal and a URL, it plans a documentation run, executes it step-by-step using raw Chrome DevTools Protocol, critiques the output, and produces a portable Markdown document with annotated screenshots.

The CLI is the first deliverable. The long-term product is a SaaS platform where teams trigger documentation runs, manage outputs, schedule re-runs when the app changes, and export to any destination (Notion, Confluence, PDF, custom templates).

---

## 2. Problem

Writing user documentation for SaaS products is:

- **Manual and slow** — a single feature walkthrough can take hours to capture, annotate, and format.
- **Always out of date** — every UI change requires a full re-shoot.
- **Inconsistent** — different writers produce different quality; annotations are subjective.
- **Expensive** — technical writers or PMs spend significant time on documentation that is not core product work.

Existing solutions either require a human to operate a screen recorder (Loom, Scribe) or are rigid macros that break when the UI changes (Selenium scripts). None use an LLM to plan, adapt, and critique the documentation in a single automated loop.

---

## 3. Solution & Product Vision

browser-auto closes this gap with a three-agent pipeline:

1. **Planner** — given a goal and the current page DOM, generates a step-by-step plan.
2. **Executor** — converts each step into a CDP action, verifies success, retakes on failure, and annotates each screenshot with a vision LLM.
3. **Critic** — reviews the generated docs, scores quality, identifies gaps, and triggers re-execution of missing steps.

The loop is self-healing: URL drift triggers replanning, failed steps are retried with different strategies, and the critic closes coverage gaps automatically.

**Vision:** A documentation platform where any developer or PM can type a goal, point at a URL, and receive publishable docs in minutes — with the option to schedule re-runs, push to destinations like Confluence or Notion, and track documentation coverage over time.

---

## 4. Target Users

| Persona | Pain | How browser-auto helps |
|---------|------|------------------------|
| **Solo developer / indie hacker** | No time or budget for a technical writer | Run the CLI after each feature ship; get docs immediately |
| **Product team at a startup** | UI changes faster than docs can keep up | Schedule re-runs on deploy; always-fresh docs |
| **Technical writer** | Spends hours on screenshots and step-by-step captures | Generate a first draft automatically; edit and publish |
| **QA / DevOps** | Needs regression documentation after releases | Automated doc run as part of CI/CD pipeline |

---

## 5. Core Features (Current — CLI v0.1)

### Pipeline
- Planner → Executor → Critic agentic loop
- Post-action verification with DOM diff (MD5 hash)
- Retry loop with 3 fallback strategies per step
- Critic re-execution of missing steps
- URL drift detection and automatic replanning

### Browser Automation
- Raw CDP over WebSocket (no Playwright / Selenium dependency)
- Shadow DOM and same-origin iframe traversal
- Visual element highlighting before each screenshot
- Automatic Chrome launch with platform-aware binary detection

### Authentication
- Session cookie injection from JSON file (EditThisCookie format)
- Auto-login: detects email/password forms, fills with React-compatible native setter
- Secure credential escaping via `json.dumps()`

### Output
- Portable Markdown + screenshots directory
- Auto-generated table of contents
- Collapsible technical details per step
- `--title` flag for clean H1 separate from the LLM goal
- Annotation language anchored to `--goal` language, not UI text

### LLM / Cost
- LiteLLM backend — any provider (OpenAI, Anthropic, Google, etc.)
- `--annotation-model` for cost optimization (cheap model for vision, better model for planning)
- Rate limit retry with exponential backoff

---

## 6. Architecture Overview

```
CLI (main.py)
  └── AgenticLoop (src/agent/loop.py)
        ├── Auth setup (cookies / credential login)
        ├── Planner (src/agent/planner.py)
        │     └── PLANNER_SYSTEM prompt → step list
        ├── Executor loop (src/agent/executor.py)
        │     ├── execute_step() with DOM verification
        │     ├── CDP actions via InputDomain / PageDomain
        │     ├── Vision annotation via LLMClient
        │     └── URL drift → PLANNER_REPLAN_SYSTEM
        ├── Critic loop (src/agent/critic.py)
        │     ├── CRITIC_SYSTEM → score + missing_steps
        │     └── missing_steps → PLANNER_SUPPLEMENT_SYSTEM → re-execute
        └── DocRenderer (src/docs/renderer.py)
              └── save_docs() → docs.md + screenshots/

CDP Layer (src/cdp/)
  ├── CDPConnection — raw WebSocket JSON-RPC
  ├── PageDomain — navigate, screenshot, DOM
  ├── InputDomain — click, type, scroll
  └── RuntimeDomain — JS eval, interactive elements, shadow DOM

LLM Layer (src/llm/)
  ├── LLMClient — async LiteLLM wrapper, vision, retry
  └── prompts.py — 7 prompt templates
```

**Key invariant:** No browser automation framework dependency. All browser control flows through raw CDP messages in `src/cdp/`.

---

## 7. Roadmap

### Phase 1 — CLI Stabilization (next)

Focus: fill the remaining output quality and robustness gaps before building the platform layer.

| Item | Priority | Notes |
|------|----------|-------|
| `--language` flag | High | Single flag controlling output language for renderer labels, planner, and annotator. Unblocks consistent multilingual docs. |
| Network idle detection | Medium | Replace `asyncio.sleep` settle waits with CDP `Page.lifecycleEvent` for deterministic page-ready detection |
| Resume from checkpoint | Medium | Save progress after each step; resume after crash |
| Better failed-step recovery | Medium | Skip or retry `[FAILED]` steps instead of embedding inline |
| `--config` YAML/TOML file | Low | Read all CLI options from a config file for repeatable runs |
| Dry-run / plan-only mode | Low | Print the planned steps without executing |

### Phase 2 — API Server

Turn browser-auto into an HTTP service so other tools can call it programmatically.

- FastAPI wrapper around `AgenticLoop.run()`
- Job queue (async task, status polling endpoint)
- Webhook callback on completion
- Docker image for self-hosting
- OpenAPI spec for the API

### Phase 3 — Web UI & Dashboard

Browser-based interface for non-technical users.

- Trigger documentation runs (URL + goal form)
- Live run progress (step-by-step stream)
- Output viewer (rendered docs + screenshot gallery)
- Run history and diffing (what changed since last run)
- Authentication (user accounts, API keys)

### Phase 4 — Scheduled Runs & Integrations

- Schedule re-runs on a cron or on deploy webhook
- Destination integrations: Confluence, Notion, GitHub Pages, PDF
- Slack / email notifications on completion or quality regression
- Custom output templates (Jinja2)

### Phase 5 — SaaS Platform

- Multi-tenant hosted service
- Subscription tiers (run limits, model choice, storage)
- Team collaboration (shared runs, comments, approvals)
- Usage analytics and documentation coverage tracking

---

## 8. Success Metrics

| Metric | Target |
|--------|--------|
| CLI time-to-first-docs | < 5 min for a 10-step flow |
| Critic score on generated docs | ≥ 7/10 on first pass for standard SaaS flows |
| Failed steps per run | < 10% of total steps |
| Test coverage | All agent, CDP, and rendering units covered |
| Annotation language consistency | 100% match with `--goal` language |

---

## 9. Non-Goals (Current Scope)

- **Video recording** — out of scope; screenshots per step is the format.
- **Cross-origin iframes** — CDP restricts access; not planned unless CDP exposes a path.
- **Multi-tab flows** — not supported in the current single-session model.
- **MFA / TOTP** — complex auth flows require manual intervention for now.
- **Generating code** — the output is documentation, not test scripts.
