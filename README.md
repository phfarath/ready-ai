# ready-ai

Open-source browser automation engine that drives Chrome over **raw CDP** (no WebDriver, no Playwright relay) and returns **verifiable workflows**: every step declares actions, expectations and extractions, and every result is a structured, sanitized object — not a screenshot dump.

```python
import asyncio
from ready_ai import ReadyAI, Flow, FlowStep, FlowAction, FlowAssertion

flow = Flow(
    name="login-smoke",
    url="https://app.example.com/login",
    steps=[
        FlowStep(
            name="Sign in",
            actions=[
                FlowAction(action="type", selector="#email", text="user@example.com"),
                FlowAction(action="type", selector="#password", text="s3cret"),
                FlowAction(action="click", selector="button[type=submit]"),
            ],
            asserts=[FlowAssertion(type="url_contains", expected="/dashboard")],
        ),
    ],
)

ai = ReadyAI()
result = asyncio.run(ai.run_flow(flow))
print(result.status)  # passed | failed
```

60 seconds to first green run: `pip install -e ".[dev]"`, export one LLM key, run the snippet above against your staging URL.

## Why this engine

- **Verifiable by construction.** Steps carry `actions` + `asserts` + `extract`; `RunResult` reports per-step status, attempts and failure reasons. A step that only "looks right" but asserts nothing fails loudly.
- **Effect policy fails closed.** Flows declare `observe` / `navigate` / `interactive` ceilings; anything above the ceiling is rejected at validation time, before Chrome even launches.
- **Secrets never serialize.** Profiles are allowlist references resolved at runtime. URLs with embedded credentials are rejected. Typed text is masked at the report boundary.
- **Deterministic where it matters.** Stable flows compile to direct CDP execution; the LLM acts at authoring time and on drift — never as a per-step tax on a healthy flow (see `ENGINE-ROADMAP.md`, Fase 3).

## Precision benchmark

Precision here means: the same flow passes 50/50 on a clean tree and fails loudly (never silently green) on a mutated one.

| Signal | Status |
|--------|--------|
| Verifiable outcomes (`RunResult` per-step status/asserts/extract) | ✅ shipped (v0.1.0) |
| Semantic locators with safe-action fallback | ✅ shipped (v0.1.0) |
| Multi-causal healing gate (2 channels must agree, else `DRIFT_SUSPECTED`) | ✅ shipped (v0.1.0) |
| Local E2E matrix: SPA, Shadow DOM, cross-origin iframe, pop-up, redirect, download, dialog, CDP drop | ✅ shipped (v0.2.0) |
| Effect policy + confirmation gates (ceilings, idempotency, `pending_confirmation`) | ✅ shipped (v0.3.0) |
| Explicit tab/session contexts, allowlisted upload, verified download, explicit dialogs | ✅ shipped (v0.3.0) |
| Human checkpoint (`await_human` pause/resume) + persistent profiles, temp cleanup | ✅ shipped (v0.3.0) |
| Mutation scorecard (detection rate / false-positive rate per channel) | 🚧 Fase 4 (`READY-AI-T-PH4-DIAG-SCORE`) |

Known limits today: OAuth/SSO auto-login (human checkpoint only, by design), heavy multi-app SSO chains — Fase 2 closed; zero-token replay is Fase 3.

## What this is / is not

- **Is:** a Python-first automation engine (`ready_ai` SDK) + CLI + FastAPI service for background runs, batch and deploy webhooks.
- **Is not:** a docs generator (generation is one consumer of the engine, kept as an example), a hosted product, a dashboard, or a recorder that needs a human to click first.

## Quickstart

Prerequisites: Python `>=3.10`, Chrome/Chromium/Brave, one model key (`OPENAI_API_KEY` or `ANTHROPIC_API_KEY`). Non-default Chrome path: `export CHROME_PATH="/path/to/chrome"`.

```bash
pip install -e ".[dev]"
export OPENAI_API_KEY="your-key-here"
```

SDK (recommended for automation):

```python
import asyncio
from ready_ai import ReadyAI, Flow, FlowStep, FlowAction, BrowserOptions

flow = Flow(url="https://app.example.com", steps=[FlowStep(
    actions=[FlowAction(action="observe")],
)])
ai = ReadyAI(profiles={"qa": "./cookies.json"})
result = asyncio.run(ai.run_flow(flow, browser=BrowserOptions(profile="qa")))
```

CLI (flows and docs consumers):

```bash
ready-ai run --goal "Smoke the checkout" --url "https://app.example.com" --headless
ready-ai batch --config example-batch.yaml
ready-ai api --port 8000 --host 127.0.0.1
```

Authenticated runs: prefer a cookies JSON array file (`--cookies-file`, `profiles={...}`); username/password auto-login exists for simple forms only. Never commit `.env`, cookies, or generated output.

## Architecture & roadmap

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — engine map: CDP layer, agent loop, executor, policies, SDK boundary.
- [`ENGINE-ROADMAP.md`](ENGINE-ROADMAP.md) — pivot plan: 5 phases, expected outcome per phase, release train v0.2.0–v0.6.0.
- [`CHANGELOG.md`](CHANGELOG.md) — per-release notes.
- Consumer docs (`docs/API.md`, `docs/BATCH.md`, `docs/WEBHOOK.md`, `docs/CI-CD.md`, `docs/VERSIONING.md`, `docs/NOTIFICATIONS.md`) describe the API/batch/webhook surfaces.

## Development

```bash
python3 -m pytest -q
ruff check src/ tests/ main.py
```

CI runs lint plus pytest across Python 3.10–3.12. See `CONTRIBUTING.md` and `SECURITY.md`.
