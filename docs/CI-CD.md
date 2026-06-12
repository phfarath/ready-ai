# CI/CD Guide

> ⚠️ **Prerequisite:** `ready-ai` requires Google Chrome, Chromium, or Brave to be installed on the machine where it runs. Without a Chrome binary available, the tool will fail with `FileNotFoundError: Chrome not found`.
>
> - **GitHub Actions:** The workflows below use `ubuntu-latest` runners and install Chromium automatically via `apt-get`.
> - **Windows / macOS runners:** You must install Chrome first (e.g., `choco install googlechrome` on Windows or `brew install --cask google-chrome` on macOS) or set the `CHROME_PATH` environment variable.

Integrate ready-ai into your continuous integration and deployment pipelines.

## GitHub Actions

### Reusable Action

Use the provided composite action at `.github/actions/ready-ai/action.yml`:

```yaml
name: Generate Documentation

on:
  push:
    tags:
      - "v*"

jobs:
  docs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Generate docs
        uses: ./.github/actions/ready-ai
        with:
          command: batch
          config: .ready-ai.yaml
          api-key: ${{ secrets.OPENAI_API_KEY }}
          app-version: ${{ github.ref_name }}
          git-commit: ${{ github.sha }}
```

### Full Workflow (`.github/workflows/docs-generation.yml`)

The repo includes a complete workflow at `.github/workflows/docs-generation.yml`. It:

1. Triggers on tag push (`v*`) or manual dispatch
2. Installs Chrome and Python dependencies
3. Determines version from tag or input
4. Runs the batch config (or falls back to a default flow)
5. Uploads documentation as artifact
6. Commits docs to a `docs/{version}` branch

### Regression Test Workflow

Add this to catch documentation drift on PRs:

```yaml
name: Documentation Regression

on:
  pull_request:
    paths:
      - "frontend/**"
      - "src/**"

jobs:
  test-docs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup
        uses: ./.github/actions/ready-ai
        with:
          command: test
          doc: ./docs/latest/docs.md
          url: https://staging.example.com
          threshold: 0.85
          api-key: ${{ secrets.OPENAI_API_KEY }}

      - name: Upload report
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: doc-regression-report
          path: ./output/test-report/
```

## Docker

### Dockerfile

Create a `Dockerfile` in your project:

```dockerfile
FROM python:3.12-slim

# Install Chrome dependencies
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    xdg-utils \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Set Chrome path
ENV CHROME_PATH=/usr/bin/chromium

# Install ready-ai
WORKDIR /app
COPY pyproject.toml ./
RUN pip install ready-ai

# Copy batch config
COPY .ready-ai.yaml ./

CMD ["ready-ai", "batch", "--config", ".ready-ai.yaml"]
```

### Docker Compose

```yaml
version: "3.8"

services:
  ready-ai:
    build: .
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - NOTIFY_WEBHOOK_URL=${NOTIFY_WEBHOOK_URL}
    volumes:
      - ./output:/app/output
      - ./.ready-ai.yaml:/app/.ready-ai.yaml:ro
```

### Build and Run

```bash
docker build -t ready-ai .
docker run -e OPENAI_API_KEY=$OPENAI_API_KEY ready-ai
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | yes | LLM provider API key |
| `CHROME_PATH` | no | Chrome binary path |
| `NOTIFY_WEBHOOK_URL` | no | Notification webhook |
| `APP_VERSION` | no | App version fallback |
| `GITHUB_SHA` | no | Git commit fallback |
| `DEPLOYED_AT` | no | Deploy timestamp |
| `READY_AI_WEBHOOK_URL` | no | Your ready-ai instance URL |

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success (all passed) |
| `1` | Broken steps (execution failed) |
| `2` | UI drift detected |
| `3` | Batch partially failed |

## Best Practices

1. **Pin versions** — lock `ready-ai` version in `pyproject.toml` or `requirements.txt`
2. **Use staging** — run tests against staging before production
3. **Artifact retention** — keep docs artifacts for 30 days minimum
4. **Parallel jobs** — run docs generation in parallel with tests
5. **Notifications** — configure webhook to alert on drift/failure
6. **Selective triggers** — only run on frontend PRs (use `paths:` filter)

## Troubleshooting

### Chrome not found
```bash
export CHROME_PATH=$(which chromium-browser || which chromium || which google-chrome)
```

### Port pool exhausted
Reduce concurrent flows or increase the port range in `src/api/manager.py`.

### LLM rate limits
Add `sleep` between flows or use a model with higher rate limits.

## CDP Auto-Reconnect (P0-1)

The CDP layer can survive transient WebSocket drops (Chrome
restarted by OOM, CI sandbox hiccup, devbox GC pause) without
forcing a full `BrowserSession.recover()`. Opt in:

```bash
export READY_AI_CDP_AUTORECONNECT=true
```

When enabled, a dropped socket triggers a background reconnect
loop with exponential backoff. The orchestrator
(`BrowserSession`) exposes `is_disconnected` so the agent loop
can call `recover()` only when the auto-reconnect has truly
exhausted its budget (the circuit breaker is open).

### Tunables

All thresholds are env-driven and default to safe values.
Tune in CI to match your flake profile.

| Variable | Default | Purpose |
| --- | --- | --- |
| `READY_AI_CDP_AUTORECONNECT` | `false` | Master switch for the whole reconnect/CB machinery. |
| `READY_AI_CB_THRESHOLD` | `3` | Consecutive failures inside the window that open the circuit. |
| `READY_AI_CB_WINDOW_S` | `60` | Sliding-window size for the consecutive-failure counter (seconds). |
| `READY_AI_CB_MAX_ATTEMPTS` | `5` | Reconnect attempts before the circuit is forced open. |
| `READY_AI_CB_BASE_S` | `0.05` | Initial backoff delay (seconds). |
| `READY_AI_CB_CAP_S` | `5.0` | Backoff cap (seconds); doubling saturates here. |
| `READY_AI_CB_REATTACH_WAIT_S` | `3.0` | How long to wait for `Target.attachedToTarget` before falling back to a manual re-attach. |

### Failure modes

* **Circuit open (3 failures in 60s):** `CDPConnection.send`
  raises `WebSocketDisconnected` immediately. `BrowserSession.
  is_disconnected` returns `True`. The agent loop should call
  `recover()`.
* **Single transient drop:** auto-reconnect succeeds within
  1-2 attempts. The FSM returns to `HEALTHY`; the agent
  loop never notices anything was wrong.
* **Heartbeat (ping) failure:** the `websockets` library
  closes the socket after `ping_timeout=10` seconds of
  silence, the recv loop catches `ConnectionClosed`, and
  the auto-reconnect path takes over.

### Observability

* `cdp.disconnects{intentional=true|false}` — counter, fires
  on every socket-down event.
* `cdp.circuit.opens` — counter, fires once per circuit-open
  transition.
* `cdp.reconnect.attempts{outcome=success|failure}` — counter,
  one increment per attempt.
* `cdp.reconnect.exhausted` — counter, fires when the budget
  is spent without a successful reconnect.
