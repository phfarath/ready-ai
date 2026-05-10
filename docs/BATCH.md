# Batch Runner Guide

Run multiple documentation flows from a single configuration file.

## Quick Start

```bash
ready-ai batch --config example-batch.yaml
```

## Configuration Format

### YAML

```yaml
app_version: "2.3.1"
git_commit: "abc1234"
deployed_at: "2026-05-09T14:00:00Z"
base_url: "https://app.example.com"

model: "gpt-4o-mini"
headless: true
cookies_file: "./cookies.json"

flows:
  - goal: "Document the login page"
    path: "/login"
    run_id: "v2.3.1-login"
    title: "Login Guide"
    language: "en"

  - goal: "Document user onboarding"
    path: "/welcome"
    run_id: "v2.3.1-onboarding"
    title: "Getting Started"
    language: "en"

  - goal: "Document checkout"
    path: "https://checkout.example.com/pay"  # absolute URL
    run_id: "v2.3.1-checkout"
    title: "Checkout Guide"
    language: "en"
```

### TOML

```toml
app_version = "2.3.1"
git_commit = "abc1234"
base_url = "https://app.example.com"
model = "gpt-4o-mini"
headless = true

[[flows]]
goal = "Document the login page"
path = "/login"
run_id = "v2.3.1-login"
title = "Login Guide"
language = "en"

[[flows]]
goal = "Document user onboarding"
path = "/welcome"
run_id = "v2.3.1-onboarding"
title = "Getting Started"
language = "en"
```

## Field Reference

### Top-level Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `app_version` | string | no | `null` | Application version being documented |
| `git_commit` | string | no | `null` | Git commit hash |
| `deployed_at` | string | no | `null` | ISO timestamp of deployment |
| `base_url` | string | no | `null` | Base URL for relative `path` values |
| `model` | string | no | `gpt-4o-mini` | LLM model |
| `headless` | boolean | no | `true` | Run Chrome headless |
| `cookies_file` | string | no | `null` | Path to session cookies JSON |
| `flows` | array | yes | — | List of flows to document |

### Flow Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `goal` | string | yes | — | Documentation goal |
| `path` | string | yes | — | URL path or absolute URL |
| `run_id` | string | no | auto | Custom run identifier |
| `title` | string | no | `null` | Document H1 title |
| `language` | string | no | `null` | Output language |
| `output` | string | no | `./output/{run_id}` | Output directory |

## URL Resolution

- If `path` starts with `http`, it's used as-is
- Otherwise, `base_url + path` is used
- If `base_url` is not set, `path` must be absolute

## Execution Behavior

1. All flows are started concurrently (limited by browser port pool: 100 ports)
2. Each flow gets its own `AgenticLoop` and Chrome instance
3. CLI polls every 5 seconds and prints progress
4. When all flows complete, a summary is printed

## API Batch Endpoint

Instead of CLI, you can POST a batch config directly:

```bash
curl -X POST http://localhost:8000/batches \
  -H "Content-Type: application/json" \
  -d @batch-config.json
```

Then poll:

```bash
curl http://localhost:8000/batches/abc12345
```

## Environment Variables

These env vars are automatically picked up by the batch runner:

| Variable | Description |
|----------|-------------|
| `APP_VERSION` | Fallback for `app_version` |
| `GITHUB_SHA` | Fallback for `git_commit` |
| `TAG_NAME` | Fallback for `app_version` |
| `DEPLOYED_AT` | Fallback for `deployed_at` |
| `OPENAI_API_KEY` | LLM API key |
| `CHROME_PATH` | Chrome binary path |
| `NOTIFY_WEBHOOK_URL` | Notification webhook |
