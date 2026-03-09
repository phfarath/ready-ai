# ready-ai

`ready-ai` is an open-source agentic browser automation tool that drives Chrome over raw CDP and generates step-by-step documentation with screenshots.

It plans a flow from the current DOM, executes the actions, critiques the result, and writes portable Markdown plus PNG screenshots to disk.

<p align="center">
  <img src="assets/ready-ai-hero.png" alt="ready-ai hero image" width="100%" />
</p>

## What This Repo Is

- A local CLI for documentation runs
- A raw Chrome DevTools Protocol engine
- A planner -> executor -> critic loop
- Markdown and screenshot generation
- An early FastAPI service scaffold

## What This Repo Is Not

- A hosted product
- A team dashboard
- A scheduling platform
- A polished integration layer
- Commercial support or SLAs

## Quickstart

### 1. Prerequisites

- Python `>=3.10`
- Google Chrome, Chromium, or Brave installed locally
- At least one model provider API key

If Chrome is installed in a non-default location, set:

```bash
export CHROME_PATH="/path/to/your/chrome"
```

### 2. Install

Canonical local install:

```bash
pip install -e ".[dev]"
```

If you need a `requirements.txt` entry point for local tooling, it resolves to the same dependency set:

```bash
pip install -r requirements.txt
```

### 3. Set an API key

```bash
export OPENAI_API_KEY="your-key-here"
# or:
# export ANTHROPIC_API_KEY="your-key-here"
```

### 4. Run a first job

```bash
ready-ai run \
  --goal "Document the login flow" \
  --url "https://app.example.com" \
  --title "Login Guide" \
  --language en \
  --model "gpt-4o-mini" \
  --output "./output/login-guide"
```

Generated files:

- `output/<run>/docs.md`
- `output/<run>/screenshots/`
- `output/<run>/summary.txt`

## Running The Project Correctly

The most reliable local setup is:

1. Install with `pip install -e ".[dev]"`
2. Export a provider API key
3. Let the tool launch its own Chrome instance
4. Start with a narrow goal and a single authenticated flow
5. Use `--verbose` on early runs

Recommended first targets:

- login
- onboarding
- account settings
- dashboard navigation
- simple CRUD flows

Avoid for first runs:

- mandatory SSO or OAuth-only login
- MFA / TOTP
- heavy multi-tab flows
- cross-origin iframe-heavy apps

## Authentication

Two auth modes are supported.

### Option 1: Session cookies

This is usually the best option for authenticated SaaS apps.

```bash
ready-ai run \
  --goal "Document account settings" \
  --url "https://app.example.com/settings" \
  --cookies-file "./cookies.json"
```

Expected `cookies.json` format:

```json
[
  {
    "name": "session_cookie",
    "value": "abc123",
    "domain": ".app.example.com",
    "path": "/",
    "secure": true,
    "httpOnly": true
  }
]
```

Notes:

- The file must be a JSON array, not a key/value object.
- Keep cookie files out of Git.
- `tmp/` is gitignored in this repository.

### Option 2: Username/password

```bash
ready-ai run \
  --goal "Document the billing page" \
  --url "https://app.example.com/login" \
  --username "user@example.com" \
  --password "super-secret-password"
```

This works best with straightforward email/password forms.

## CLI

Main command:

```bash
ready-ai run --help
```

Useful flags:

| Flag | Short | Default | Purpose |
|------|-------|---------|---------|
| `--goal` | `-g` | required | Documentation goal |
| `--url` | `-u` | required | Starting URL |
| `--title` | `-t` | goal | Optional H1 title |
| `--language` | `-l` | English renderer labels | Output language |
| `--model` | `-m` | `gpt-4o-mini` | Main planner/critic model |
| `--annotation-model` | | same as `--model` | Separate screenshot annotation model |
| `--output` | `-o` | `./output` | Output directory |
| `--port` | `-p` | `9222` | Chrome debugging port |
| `--headless` | | `false` | Run Chrome headless |
| `--max-critic-rounds` | | `2` | Max re-execution loops |
| `--cookies-file` | | `None` | Cookie JSON file |
| `--username` | | `None` | Login username/email |
| `--password` | | `None` | Login password |
| `--verbose` | `-v` | `false` | Debug logging |

### More examples

Portuguese output:

```bash
ready-ai run \
  --goal "Documentar o fluxo de onboarding" \
  --url "https://app.example.com" \
  --language pt \
  --output "./output/onboarding-pt"
```

Cheaper model for screenshot annotations:

```bash
ready-ai run \
  --goal "Document the dashboard" \
  --url "https://app.example.com" \
  --model "claude-sonnet-4-20250514" \
  --annotation-model "gpt-4o-mini"
```

## API

Start the API:

```bash
ready-ai api --port 8000
```

Main endpoints:

- `POST /runs`
- `GET /runs/{run_id}`
- `GET /runs/{run_id}/output`

Example flow:

```bash
curl -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"goal":"Document login","url":"https://app.example.com"}'

curl http://localhost:8000/runs/<run_id>
curl -OJ http://localhost:8000/runs/<run_id>/output
```

Current API capabilities:

- start a background run
- poll run status
- resume from an existing checkpoint when the same `run_id` is reused
- download output as a ZIP archive

## How It Works

```text
goal + DOM  ->  planner  ->  step list
step list   ->  executor ->  screenshots + annotations
docs        ->  critic   ->  score + missing steps -> re-execution
```

Core modules:

- `src/agent/` orchestrates planning, execution, criticism, and checkpoints
- `src/cdp/` contains the raw CDP browser engine
- `src/docs/` renders Markdown and writes artifacts
- `src/api/` exposes background runs over FastAPI
- `src/llm/` wraps LiteLLM model calls

## Current Limitations

High-priority gaps today:

- better failed-step recovery
- `--config` file support
- dry-run / plan-only mode
- OAuth / SSO coverage
- MFA / TOTP coverage
- multi-tab flows
- cross-origin iframe coverage

This repository is usable today, but the product surface is still CLI-first and early.

## Development

Run tests:

```bash
python3 -m pytest -q
```

See:

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [SECURITY.md](SECURITY.md)
- [MVP.md](MVP.md)
- [NEXT_STEPS.md](NEXT_STEPS.md)
- [PRD.md](PRD.md)

## Safety Notes

Before pushing or publishing:

- do not commit `.env`
- do not commit cookies
- do not commit generated output
- rotate any real credentials used during development
- review tracked files before pushing
