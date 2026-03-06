# browser-auto

Agentic browser automation for SaaS documentation generation.

browser-auto controls Chrome through raw CDP, plans documentation steps with an LLM, executes them, critiques the result, and writes portable Markdown plus screenshots.

## Current Status

- Local CLI is implemented and usable today.
- An HTTP API scaffold already exists for background runs and output download.
- The project is currently in **CLI hardening + API stabilization**, not "build the API from scratch".

## Architecture

```
goal + DOM  ->  Planner  ->  step list
step list   ->  Executor ->  screenshots + annotations
docs        ->  Critic   ->  score + missing steps -> re-execution
```

Core modules:

- `src/agent/` orchestrates planning, execution, criticism, and checkpoints.
- `src/cdp/` contains the raw Chrome DevTools Protocol client and domains.
- `src/docs/` renders Markdown and writes output files.
- `src/api/` exposes background runs over FastAPI.

## Installation

CLI development setup:

```bash
pip install -e ".[dev]"
```

API server and API tests currently rely on extra packages listed in `requirements.txt`:

```bash
pip install -r requirements.txt
```

## Environment

Set at least one provider API key:

```bash
export OPENAI_API_KEY="your-key-here"
# or:
# export ANTHROPIC_API_KEY="your-key-here"
```

## CLI Usage

Run a local documentation job:

```bash
python main.py run \
  --goal "Document the login flow" \
  --url "https://app.example.com" \
  --model "gpt-4o-mini" \
  --output "./output"
```

### `run` command options

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--goal` | `-g` | *required* | Documentation goal that drives the planner and critic |
| `--url` | `-u` | *required* | Starting URL |
| `--title` | `-t` | `None` | Optional H1 title; falls back to `goal` |
| `--language` | `-l` | `None` | Optional output language override; renderer labels fall back to English when omitted |
| `--model` | `-m` | `gpt-4o-mini` | Main planning/critic model |
| `--annotation-model` | | `None` | Optional separate model for screenshot annotations |
| `--output` | `-o` | `./output` | Output directory |
| `--port` | `-p` | `9222` | Chrome remote debugging port |
| `--headless` | | `false` | Run Chrome headless |
| `--max-critic-rounds` | | `2` | Maximum critic iterations |
| `--cookies-file` | | `None` | Session cookies JSON file |
| `--username` | | `None` | Username/email for auto-login |
| `--password` | | `None` | Password for auto-login |
| `--verbose` | `-v` | `false` | Verbose logging |

### CLI examples

```bash
# Portuguese output
python main.py run --goal "Documentar o fluxo de login" --url "https://app.example.com" --language pt

# Separate title from goal
python main.py run \
  --goal "Document all main features after login" \
  --url "https://app.example.com" \
  --title "MyApp User Guide"

# Cheaper model for screenshot annotations
python main.py run \
  --goal "Document the dashboard" \
  --url "https://app.example.com" \
  --model "claude-sonnet-4-20250514" \
  --annotation-model "gpt-4o-mini"

# Session auth
python main.py run \
  --goal "Document account settings" \
  --url "https://app.example.com/settings" \
  --cookies-file ./cookies.json
```

## API Usage

Start the API server:

```bash
python main.py api --port 8000
```

### `api` command options

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--port` | `-p` | `8000` | API server port |
| `--host` | | `0.0.0.0` | API server host |
| `--verbose` | `-v` | `false` | Verbose logging |

### API flow

```bash
curl -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"goal":"Document login","url":"https://app.example.com"}'

curl http://localhost:8000/runs/<run_id>
curl -OJ http://localhost:8000/runs/<run_id>/output
```

Current API capabilities:

- Start a background run
- Poll run status
- Resume from an existing checkpoint when the same `run_id` is reused
- Download generated output as a ZIP archive

Still missing from the API roadmap:

- webhooks
- hardened dependency/test stack
- Docker packaging
- external-consumer contract stabilization

## Output

CLI runs write:

- `output/docs.md`
- `output/screenshots/`
- `output/summary.txt`

API runs write per-run artifacts under `output/<run_id>/`.

## Supported Models

Any LiteLLM-compatible model, including:

- `gpt-4o-mini`, `gpt-4o`
- `claude-sonnet-4-20250514`, `claude-haiku-4-20250414`
- `gemini/gemini-2.0-flash`

## Open Product Gaps

Current high-priority gaps:

- API and test-stack stabilization
- Better failed-step recovery
- `--config` file support
- dry-run / plan-only mode
- OAuth / SSO, MFA / TOTP, multi-tab, and cross-origin iframe coverage

See `PRD.md` for product direction and `NEXT_STEPS.md` for the active execution plan.
