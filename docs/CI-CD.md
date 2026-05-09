# CI/CD Guide

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
