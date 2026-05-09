# Webhook Integration Guide

Connect your CI/CD pipeline to ready-ai via deploy webhooks.

## Overview

When your application deploys, your CI/CD system calls ready-ai's deploy webhook. ready-ai then automatically documents all configured flows for that release.

## Webhook Endpoint

```
POST /webhooks/deploy
```

## Authentication

Currently, the webhook endpoint does not require authentication. For production, protect it with:
- Reverse proxy (nginx/traefik) with IP allowlisting
- API gateway with token validation
- Cloudflare Access / AWS WAF

## Payload Schema

```json
{
  "app_version": "2.3.1",
  "git_commit": "abc1234",
  "deployed_at": "2026-05-09T14:00:00Z",
  "base_url": "https://app.example.com",
  "flows": [
    {
      "goal": "Document login",
      "path": "/login",
      "run_id": "login",
      "title": "Login Guide",
      "language": "en"
    },
    {
      "goal": "Document onboarding",
      "path": "/welcome",
      "run_id": "onboarding"
    }
  ],
  "model": "gpt-4o-mini",
  "headless": true,
  "cookies_file": null
}
```

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `app_version` | string | Version being deployed |
| `git_commit` | string | Git commit hash |
| `deployed_at` | string | ISO 8601 timestamp |
| `base_url` | string | Your app's base URL |
| `flows` | array | At least one flow required |

### Flow Object

| Field | Required | Description |
|-------|----------|-------------|
| `goal` | yes | What to document |
| `path` | yes | URL path (relative to `base_url`) or absolute URL |
| `run_id` | no | Custom ID (auto-generated if omitted) |
| `title` | no | Document title |
| `language` | no | Output language |

## Example: GitHub Actions

```yaml
name: Deploy and Document

on:
  push:
    tags:
      - "v*"

jobs:
  deploy-and-docs:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to production
        run: ./scripts/deploy.sh

      - name: Trigger documentation
        run: |
          curl -X POST ${{ secrets.READY_AI_WEBHOOK_URL }} \
            -H "Content-Type: application/json" \
            -d '{
              "app_version": "'${GITHUB_REF#refs/tags/v}'",
              "git_commit": "${{ github.sha }}",
              "deployed_at": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
              "base_url": "https://app.example.com",
              "flows": [
                {"goal": "Document login", "path": "/login"},
                {"goal": "Document dashboard", "path": "/dashboard"}
              ]
            }'
```

## Example: Vercel Deploy Hook

```javascript
// vercel.json or API route
export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).end();

  await fetch('https://ready-ai.your-company.com/webhooks/deploy', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      app_version: process.env.VERCEL_GIT_COMMIT_SHA.slice(0, 7),
      git_commit: process.env.VERCEL_GIT_COMMIT_SHA,
      deployed_at: new Date().toISOString(),
      base_url: `https://${process.env.VERCEL_URL}`,
      flows: [
        { goal: 'Document homepage', path: '/' }
      ]
    })
  });

  res.status(202).end();
}
```

## Example: GitLab CI

```yaml
document:
  stage: post-deploy
  script:
    - |
      curl -X POST $READY_AI_WEBHOOK_URL \
        -H "Content-Type: application/json" \
        -d '{
          "app_version": "'${CI_COMMIT_TAG#v}'",
          "git_commit": "'${CI_COMMIT_SHA}'",
          "deployed_at": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
          "base_url": "https://app.example.com",
          "flows": [
            {"goal": "Document login", "path": "/login"}
          ]
        }'
  only:
    - tags
```

## Response

### Accepted

```json
{
  "batch_id": "abc12345",
  "total_flows": 2,
  "accepted": 2,
  "rejected": 0,
  "run_ids": ["login", "onboarding"],
  "status": "ACCEPTED"
}
```

Status: `202 Accepted`

### Error

```json
{"detail": "Batch processing failed: ..."}
```

Status: `500 Internal Server Error`

## Polling for Completion

After receiving a `batch_id`, poll until complete:

```bash
while true; do
  STATUS=$(curl -s http://localhost:8000/batches/abc12345 | jq -r '.status')
  if [ "$STATUS" != "ACCEPTED" ]; then break; fi
  sleep 5
done
```

Or use the `ready-ai batch --config` CLI which does this automatically.

## Retrying Failed Flows

If a batch partially fails, you can retry individual runs:

```bash
curl -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"run_id": "login", "goal": "Document login", "url": "https://app.example.com/login"}'
```

## Webhook Payload from Batch Config

You can generate a webhook payload from a batch config file:

```bash
python -c "
import json, yaml
with open('flows.yaml') as f:
    config = yaml.safe_load(f)
print(json.dumps(config, indent=2))
" | curl -X POST http://localhost:8000/webhooks/deploy \
  -H 'Content-Type: application/json' \
  -d @-
```
