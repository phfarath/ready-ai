# 🤖 browser-auto

**Agentic browser automation for SaaS documentation generation.**

Uses raw Chrome DevTools Protocol (CDP) over WebSocket + LLM to navigate SaaS UIs, capture screenshots, and generate annotated Markdown documentation.

## Architecture

```
                    ┌──────────────┐
                    │     Auth     │
                    │ cookies/creds│
                    └──────┬───────┘
                           │
┌─────────────┐     ┌──────▼───────┐     ┌─────────────┐
│   Planner   │────▶│   Executor   │────▶│   Critic    │
│  (LLM)      │     │ (CDP + LLM)  │     │  (LLM)      │
│             │     │              │     │             │
│ goal + DOM  │     │ step → JSON  │     │ review docs │
│ → step plan │◀────│ URL drift →  │     │ → score/fix │
│             │     │ replan       │     └──────┬──────┘
└─────────────┘     │ → screenshot │            │
                    │ → annotate   │       (re-execute
                    └──────────────┘        missing steps)
                          │                      │
                          ▼◀────────────────────-┘
                    ┌──────────────┐
                    │   Output     │
                    │  docs.md +   │
                    │  screenshots │
                    └──────────────┘
```

## Quick Start

### 1. Install dependencies

```bash
pip install -e ".[dev]"
```

### 2. Set your API key

```bash
export OPENAI_API_KEY="your-key-here"
# or for Anthropic:
# export ANTHROPIC_API_KEY="your-key-here"
```

### 3. Run

```bash
python main.py \
  --goal "Document the login flow" \
  --url "https://app.example.com" \
  --model "gpt-4o-mini" \
  --output "./output"
```

## Options

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--goal` | `-g` | *required* | Documentation goal — drives the LLM |
| `--url` | `-u` | *required* | Target SaaS URL |
| `--title` | `-t` | *(goal)* | Document H1 title (clean heading, independent of goal) |
| `--model` | `-m` | `gpt-4o-mini` | LiteLLM-compatible model for planning/criticism |
| `--annotation-model` | | *(model)* | Separate model for screenshot annotations (use a cheaper one) |
| `--output` | `-o` | `./output` | Output directory |
| `--port` | `-p` | `9222` | Chrome remote debugging port |
| `--headless` | | `false` | Run Chrome in headless mode |
| `--max-critic-rounds` | | `2` | Max Critic review iterations |
| `--cookies-file` | | | JSON cookies file for session auth (EditThisCookie format) |
| `--username` | | | Email/username for auto-login |
| `--password` | | | Password for auto-login |
| `--verbose` | `-v` | `false` | Enable debug logging |

## Examples

```bash
# Basic documentation run
python main.py --goal "Document the onboarding flow" --url "https://app.example.com"

# Clean title separate from the goal description
python main.py \
  --goal "Document all main features: navigation, pages, and interactive elements after login" \
  --url "https://app.example.com" \
  --title "MyApp — User Guide"

# Cost-optimized: Claude for planning, gpt-4o-mini for annotations
python main.py \
  --goal "Document the dashboard" \
  --url "https://app.example.com" \
  --model "claude-sonnet-4-20250514" \
  --annotation-model "gpt-4o-mini"

# Authenticated session via cookies
python main.py \
  --goal "Document account settings" \
  --url "https://app.example.com/settings" \
  --cookies-file ./cookies.json

# Auto-login with credentials
python main.py \
  --goal "Document post-login features" \
  --url "https://app.example.com" \
  --username "user@email.com" \
  --password "mysecret"

# Headless + verbose debug logging
python main.py \
  --goal "Document sign-up" \
  --url "https://app.example.com/signup" \
  --headless \
  --verbose
```

## Output

The tool generates:
- `output/docs.md` — Full documentation with screenshots
- `output/screenshots/` — Step-by-step PNG screenshots (`step_01.png`, `step_02.png`, ...)
- `output/summary.txt` — Generation summary

## Supported Models

Any [LiteLLM-compatible model](https://docs.litellm.ai/docs/providers):
- `gpt-4o-mini`, `gpt-4o` (OpenAI)
- `claude-sonnet-4-20250514`, `claude-haiku-4-20250414` (Anthropic)
- `gemini/gemini-2.0-flash` (Google)

## Requirements

- Python 3.10+
- Google Chrome or Chromium installed
- At least one LLM API key
