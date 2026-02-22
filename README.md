# 🤖 browser-auto

**Agentic browser automation for SaaS documentation generation.**

Uses raw Chrome DevTools Protocol (CDP) over WebSocket + LLM to navigate SaaS UIs, capture screenshots, and generate annotated Markdown documentation.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Planner   │────▶│   Executor   │────▶│   Critic    │
│  (LLM)      │     │ (CDP + LLM)  │     │  (LLM)      │
│             │     │              │     │             │
│ goal + DOM  │     │ step → JSON  │     │ review docs │
│ → step plan │     │ → CDP action │     │ → score/fix │
└─────────────┘     │ → screenshot │     └─────────────┘
                    │ → annotate   │           │
                    └──────────────┘           │
                          │               (iterate)
                          ▼
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
  --goal "Documentar fluxo de login" \
  --url "https://app.example.com" \
  --model "gpt-4o-mini" \
  --output "./output"
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--goal`, `-g` | *required* | Documentation goal |
| `--url`, `-u` | *required* | Target SaaS URL |
| `--model`, `-m` | `gpt-4o-mini` | LiteLLM-compatible model |
| `--output`, `-o` | `./output` | Output directory |
| `--port`, `-p` | `9222` | Chrome debugging port |
| `--headless` | `false` | Run Chrome headless |
| `--verbose`, `-v` | `false` | Debug logging |

## Output

The tool generates:
- `output/docs.md` — Full documentation with screenshots
- `output/screenshots/` — Step-by-step PNG screenshots
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
