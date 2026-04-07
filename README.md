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
- A documentation test runner with visual diff and self-healing
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

### 5. Test your documentation against the live UI

Once you have generated documentation, you can verify it still matches the live application:

```bash
ready-ai test \
  --doc "./output/login-guide/docs.md" \
  --url "https://app.example.com" \
  --threshold 0.85 \
  --output "./test-report"
```

This re-executes every documented step, takes new screenshots, and compares them with the baselines. The output includes a JSON report, a plain-text summary, and a standalone HTML report.

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

### `run` command

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
| `--config` | | `None` | Load run settings from a flat YAML/TOML file |
| `--run-id` | | `local_run` | Checkpoint identity inside the output directory |
| `--resume` | | `false` | Resume from an existing checkpoint |
| `--plan-only` | | `false` | Open the page, build the plan, save checkpoint, skip execution |
| `--verbose` | `-v` | `false` | Debug logging |

### `test` command

The `test` subcommand re-executes every step in a previously generated `docs.md` against the live UI, compares screenshots with baselines, and generates a test report.

```bash
ready-ai test --help
```

| Flag | Short | Default | Purpose |
|------|-------|---------|---------|
| `--doc` | `-d` | required | Path to the `docs.md` file to test |
| `--url` | `-u` | required | Target URL to test against |
| `--model` | `-m` | `gpt-4o-mini` | LLM model for vision and healing |
| `--threshold` | | `0.85` | Visual similarity threshold (0.0-1.0) |
| `--output` | `-o` | `./test-report` | Test report output directory |
| `--port` | `-p` | `9222` | Chrome debugging port |
| `--headless` | | `false` | Run Chrome headless |
| `--cookies-file` | | `None` | Cookie JSON file for authentication |
| `--username` | | `None` | Login username/email |
| `--password` | | `None` | Login password |
| `--watch` | | `false` | Re-run tests periodically until interrupted |
| `--watch-interval` | | `5` | Minutes between watch runs |
| `--auto-heal` | | `false` | Auto-update docs when drift is detected |
| `--open-pr` | | `false` | After auto-heal, commit to a new branch and open a PR (implies `--auto-heal`) |
| `--pr-base-branch` | | `dev` | Base branch for the auto-heal PR |
| `--pr-remote` | | `origin` | Git remote used to push the auto-heal branch |
| `--pr-dry-run` | | `false` | Run git steps locally but skip `push` and PR creation |
| `--verbose` | `-v` | `false` | Debug logging |

Exit codes:

- `0` — all steps passed
- `1` — one or more steps are broken (execution failed)
- `2` — UI drift detected (visual similarity below threshold)

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

Reusable config file:

```yaml
goal: Document the login flow
url: https://app.example.com/login
output: ./output/login-flow
run_id: login-flow
plan_only: true
headless: true
```

```bash
ready-ai run --config ./ready-ai.yaml
ready-ai run --config ./ready-ai.yaml --resume
```

Basic documentation test:

```bash
ready-ai test \
  --doc "./output/login-guide/docs.md" \
  --url "https://app.example.com" \
  --headless
```

Watch mode (re-test every 10 minutes, alert on drift):

```bash
ready-ai test \
  --doc "./output/login-guide/docs.md" \
  --url "https://app.example.com" \
  --watch \
  --watch-interval 10 \
  --headless
```

Auto-heal mode (update screenshots and annotations automatically):

```bash
ready-ai test \
  --doc "./output/login-guide/docs.md" \
  --url "https://app.example.com" \
  --auto-heal \
  --headless
```

Auto-heal + auto-PR (dry-run — commits locally, no push/PR):

```bash
ready-ai test \
  --doc "./output/login-guide/docs.md" \
  --url "https://app.example.com" \
  --open-pr \
  --pr-dry-run \
  --pr-base-branch dev \
  --headless
```

Auto-heal + auto-PR (full flow — requires `gh` CLI authenticated):

```bash
ready-ai test \
  --doc "./output/login-guide/docs.md" \
  --url "https://app.example.com" \
  --open-pr \
  --pr-base-branch dev \
  --headless
```

> **Safety:** `--open-pr` implies `--auto-heal`. The publisher aborts if the
> working tree has changes outside the healed files, so it will never hijack
> unrelated in-progress work. Use `--pr-dry-run` first to inspect the branch
> locally before pushing. A ready-to-use GitHub Actions workflow ships at
> `.github/workflows/self-heal.yml` — it runs the command above on demand,
> uploads the HTML report as an artifact, and opens the PR against `dev`.

## Self-Healing Documentation

The `test` command powers a self-healing documentation pipeline. Instead of documentation going stale when the UI changes, ready-ai detects drift and can automatically fix it.

### How it works

1. **Parse** -- The doc parser (`src/docs/parser.py`) extracts executable steps, screenshots, and action descriptions from a generated `docs.md`. Supports multilingual step headers (English, Portuguese, Spanish, French, German, Italian).

2. **Re-execute** -- Each step is replayed against the live UI using the same CDP executor that generated the original documentation.

3. **Visual diff** -- New screenshots are compared pixel-by-pixel against the baselines. A similarity score (0.0--1.0) determines whether the step is PASSED, DRIFT, or BROKEN. A side-by-side diff image is generated highlighting changed regions in red.

4. **Semantic diff** -- When drift is detected, an LLM vision call compares the baseline and current screenshots side-by-side and produces a human-readable description of what changed (e.g., "The Save button moved from the top-right to a sticky footer bar").

5. **Selector recovery** -- If a step fails because its CSS selector no longer matches any element, the LLM inspects the current interactive elements on the page and finds an equivalent selector automatically.

6. **Auto-heal** -- With `--auto-heal`, drifted steps are repaired in place:
   - The baseline screenshot is replaced with the current one
   - The annotation text is regenerated via LLM vision
   - The `docs.md` file is rewritten with the updated content

### Watch mode

Use `--watch` to run tests on a recurring interval. The runner alerts when a previously passing step starts drifting, making it suitable for CI or monitoring dashboards:

```bash
ready-ai test --doc ./output/docs.md --url https://app.example.com --watch --watch-interval 5
```

Press `Ctrl+C` to stop. Status transitions (e.g., PASSED to DRIFT_DETECTED) trigger a terminal bell alert.

### Report outputs

Every test run produces three report files in the output directory:

| File | Description |
|------|-------------|
| `test_report.json` | Machine-readable full report with per-step results |
| `test_summary.txt` | Plain-text summary (pass/drift/broken counts) |
| `test_report.html` | Standalone HTML report with inline CSS, base64-embedded screenshots and diff images, expandable step cards, and a summary table |

The HTML report can be opened in any browser with no external dependencies.

## API

Start the API:

```bash
ready-ai api --port 8000 --host 127.0.0.1
```

Flags:

| Flag | Short | Default | Purpose |
|------|-------|---------|---------|
| `--port` | `-p` | `8000` | API server port |
| `--host` | | `0.0.0.0` | API server host/interface to bind |
| `--verbose` | `-v` | `false` | Debug logging |

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
docs.md     ->  test     ->  re-execute + visual diff -> report / auto-heal
```

Core modules:

- `src/agent/` orchestrates planning, execution, criticism, checkpoints, and the doc test runner
- `src/cdp/` contains the raw CDP browser engine
- `src/docs/` renders Markdown, parses docs, generates HTML reports, runs visual and semantic diffs, and auto-heals documentation
- `src/api/` exposes background runs over FastAPI
- `src/llm/` wraps LiteLLM model calls (text, single-image vision, and multi-image vision)

### Key modules added in recent releases

| Module | Purpose |
|--------|---------|
| `src/agent/test_runner.py` | `DocTestRunner` — re-executes documented steps and produces a `DocTestReport` |
| `src/docs/parser.py` | Extracts executable steps from `docs.md` (multilingual support) |
| `src/docs/visual_diff.py` | Pixel-level screenshot comparison with diff image generation |
| `src/docs/semantic_diff.py` | LLM-powered natural-language description of visual changes |
| `src/docs/auto_healer.py` | Auto-updates screenshots, annotations, and selectors when drift is detected |
| `src/docs/report_html.py` | Generates standalone HTML test reports with inline images |
| `src/docs/terminal_output.py` | Colored terminal progress bar and summary table (respects `NO_COLOR`) |

## Current Limitations

High-priority gaps today:

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

Run tests with coverage:

```bash
python3 -m pytest -q --cov=src --cov-report=term-missing
```

Lint:

```bash
ruff check src/ tests/ main.py
```

### CI

The GitHub Actions pipeline (`.github/workflows/tests.yml`) runs on every push and pull request:

- **Lint job** -- `ruff check` against `src/`, `tests/`, and `main.py`
- **Test job** -- `pytest` with coverage across Python 3.10, 3.11, and 3.12. Coverage reports are uploaded as artifacts for the 3.12 run.

See:

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [SECURITY.md](SECURITY.md)

## Safety Notes

Before pushing or publishing:

- do not commit `.env`
- do not commit cookies
- do not commit generated output
- rotate any real credentials used during development
- review tracked files before pushing
