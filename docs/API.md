# API Reference

Base URL: `http://localhost:8000`

All endpoints return JSON. Error responses follow RFC 7807-style format:
```json
{"detail": "Human-readable error message"}
```

---

## Single Run Endpoints

### `POST /runs`

Start a new documentation run.

**Request body** (JSON):
```json
{
  "run_id": "optional-custom-id",
  "goal": "Document the login page",
  "url": "https://app.example.com/login",
  "model": "gpt-4o-mini",
  "annotation_model": null,
  "language": "en",
  "title": "Login Guide",
  "headless": true,
  "cookies_file": null,
  "app_version": "2.3.1",
  "git_commit": "abc1234",
  "deployed_at": "2026-05-09T14:00:00Z"
}
```

**Response** (`200 OK`):
```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "PLANNING",
  "goal": "Document the login page",
  "url": "https://app.example.com/login",
  "executed_steps": 0,
  "total_planned_steps": 0,
  "last_known_url": null,
  "error": null
}
```

**Error responses**:
- `409 Conflict` — run with this ID is already active
- `503 Service Unavailable` — no browser ports available

---

### `GET /runs/{run_id}`

Poll the status of a specific run.

**Path params**:
- `run_id` — the run identifier

**Response** (`200 OK`):
Same shape as `POST /runs` response. Status progresses:
`PLANNING` → `EXECUTING` → `CRITIQUE` → `FINISHED` | `FAILED`

**Error responses**:
- `404 Not Found` — run does not exist

---

### `GET /runs/{run_id}/output`

Download the generated documentation as a ZIP archive.

**Response** (`200 OK`):
Returns `application/zip` with file named `browser_docs_{run_id}.zip`

The ZIP contains:
- `docs.md` — generated documentation
- `manifest.json` — run metadata
- `screenshots/` — PNG screenshots per step
- `changelog.md` — diff from previous version (if applicable)

**Error responses**:
- `404 Not Found` — output not ready yet

---

### `GET /runs/{run_id}/metrics`

Retrieve observability metrics for a completed run.

**Response** (`200 OK`):
```json
{
  "execution_time_seconds": 45.2,
  "llm_calls": 12,
  "screenshots_taken": 8,
  "steps_executed": 7,
  "steps_replanned": 1
}
```

**Error responses**:
- `404 Not Found` — metrics not found for this run

---

### `GET /runs`

List all documentation runs with optional filtering and pagination.

**Query params**:
- `status_filter` — filter by run status (e.g. `FINISHED`, `FAILED`)
- `app_version` — filter by application version (from `manifest.json`)
- `limit` (default `50`, max `200`) — page size
- `offset` (default `0`) — page start

**Response** (`200 OK`):
```json
{
  "total": 2,
  "runs": [
    {
      "run_id": "login",
      "status": "FINISHED",
      "goal": "Document login",
      "url": "https://app.example.com/login",
      "executed_steps": 5,
      "total_planned_steps": 5,
      "app_version": "2.3.1",
      "git_commit": "abc1234",
      "deployed_at": "2026-05-09T14:00:00Z",
      "created_at": "2026-05-09T14:00:00Z"
    }
  ]
}
```

---

### `GET /runs/{run_id}/diff`

Textual diff between this run's `docs.md` and a previous version.

**Query params**:
- `previous_run_id` — explicit run to compare against. When omitted, the
  server picks the most recent run with the same `app_version` (from
  `manifest.json`), falling back to the most recent other run.

**Response** (`200 OK`):
```json
{
  "run_id": "login-v2",
  "previous_version": "login-v1",
  "current_version": "login-v2",
  "changelog": "## Changelog\n\n...",
  "diff_json": {}
}
```

**Error responses**:
- `404 Not Found` — run, `docs.md`, or any previous version not found
- `500 Internal Server Error` — diff generation failed

---

### `POST /runs/{run_id}/export`

Export a completed run to a static-site format.

**Request body** (JSON):
```json
{
  "format": "docusaurus",
  "output_dir": null
}
```
Supported `format` values: `markdown`, `docusaurus`, `nextra`,
`mintlify`, `starlight`, `skill`. When `output_dir` is omitted the export lands
in `./output/{run_id}/export/{format}/`.

`skill` is experimental (US9): exports an Agent Skill (`SKILL.md` +
`screenshots as assets/` + `skill.json` verification manifest) and
requires `READY_AI_EXPERIMENTAL=1` on the server. The manifest binds
the skill to declared verification metadata (`run_id`,
`app_version`, `verified_at`, `result`) — `result` stays `"unknown"`
unless the caller declares otherwise.

**Response** (`200 OK`):
```json
{
  "run_id": "login",
  "format": "docusaurus",
  "output_dir": "./output/login/export/docusaurus",
  "files_created": ["./output/login/export/docusaurus/index.md"],
  "success": true
}
```

**Error responses**:
- `404 Not Found` — run or `docs.md` not found
- `409 Conflict` — run is not completed (export requires a finished run)
- `400 Bad Request` — unknown format

---

## Flow Endpoints

### `POST /flows/run`

Execute a declarative run-flow (actions, asserts, extractions) over the
same browser/CDP core as the documentation pipeline — without
DocRenderer, screenshots, or annotation.

**Request body** (JSON):
```json
{
  "name": "login-smoke",
  "url": "https://app.example.com/login",
  "steps": [
    {
      "name": "Submit login",
      "actions": [
        {"action": "fill", "selector": "#email", "text": "user@example.com"},
        {"action": "click", "selector": "#submit"}
      ],
      "asserts": [
        {"type": "url_contains", "expected": "/dashboard"}
      ],
      "extract": []
    }
  ],
  "retries": 1,
  "headless": true,
  "run_id": "login-smoke-1",
  "effect_policy": "write"
}
```

Each request gets its own browser port from the pool and runs under a
300s budget — a timeout is reported as `503` instead of hanging.

**Response** (`200 OK`):
```json
{
  "run_id": "login-smoke-1",
  "flow": "login-smoke",
  "url": "https://app.example.com/login",
  "status": "passed",
  "steps": [],
  "summary": {},
  "failure_reason": null
}
```
`status` is `passed` or `failed`.

**Error responses**:
- `422 Unprocessable Entity` — flow validation failed
- `500 Internal Server Error` — run-flow failed
- `503 Service Unavailable` — run-flow timed out after 300s

---

## Documentation Sets

### `GET /doc-sets`

List all generated documentation sets (one per run output directory).

**Query params**:
- `version` — filter by `app_version` (from `manifest.json`)
- `status_filter` — filter by run status
- `limit` (default `50`, max `200`)
- `offset` (default `0`)

**Response** (`200 OK`):
```json
{
  "total": 1,
  "docs": [
    {
      "run_id": "login",
      "version": "2.3.1",
      "goal": "Document login",
      "url": "https://app.example.com/login",
      "status": "FINISHED",
      "files": ["docs.md", "manifest.json", "screenshots/step_01.png"],
      "generated_at": "2026-05-09T14:00:00Z"
    }
  ]
}
```

---

### `GET /docs/{version}/status`

Health status of the documentation for a specific app version (most
recent run carrying that `app_version`).

**Response** (`200 OK`):
```json
{
  "version": "2.3.1",
  "run_id": "login",
  "status": "FINISHED",
  "steps_count": 5,
  "screenshots_count": 5,
  "has_manifest": true,
  "has_metrics": true,
  "last_tested": null,
  "test_status": null
}
```

**Error responses**:
- `404 Not Found` — no documentation found for this version

---

## Historical Tracking

Run metrics are appended to `./output/.history.jsonl` on every
completed run (docs and flows), so these endpoints serve real data —
not just the current session.

### `GET /history`

Retrieve historical run metrics, newest first.

**Query params**:
- `app_version` — filter by application version
- `status` — filter by run status
- `limit` (default `50`, max `200`)
- `offset` (default `0`)

**Response** (`200 OK`):
```json
{
  "total": 1,
  "records": [
    {
      "run_id": "login",
      "goal": "Document login",
      "status": "passed",
      "steps_count": 5,
      "llm_tokens": 1200,
      "duration_sec": 45.2,
      "app_version": "2.3.1",
      "timestamp": "2026-05-09T14:00:00Z"
    }
  ]
}
```

---

### `GET /history/aggregates`

Aggregate statistics over all historical runs.

**Query params**:
- `app_version` — restrict to one application version

**Response** (`200 OK`):
```json
{
  "total_runs": 10,
  "pass_rate": 0.9,
  "broken_rate": 0.1,
  "drift_events": 2,
  "auto_healed": 1,
  "total_steps": 48,
  "avg_steps_per_run": 4.8,
  "total_tokens": 12000,
  "avg_tokens_per_run": 1200,
  "total_duration_sec": 452.0,
  "avg_duration_sec": 45.2
}
```
When no runs are recorded the response is `{"total_runs": 0}`.

---

## Batch Endpoints

### `POST /webhooks/deploy`

Receive a deploy event and kick off documentation for all configured flows.

**Request body** (JSON):
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

**Response** (`202 Accepted`):
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

**Error responses**:
- `500 Internal Server Error` — batch processing failed

---

### `POST /batches`

Start a batch from a configuration object.

**Request body** (JSON):
```json
{
  "app_version": "2.3.1",
  "git_commit": "abc1234",
  "deployed_at": "2026-05-09T14:00:00Z",
  "base_url": "https://app.example.com",
  "model": "gpt-4o-mini",
  "headless": true,
  "flows": [
    {
      "goal": "Document login",
      "path": "/login",
      "run_id": "login",
      "title": "Login Guide",
      "language": "en"
    }
  ]
}
```

**Response** (`202 Accepted`):
Same shape as `POST /webhooks/deploy`.

---

### `GET /batches/{batch_id}`

Poll the status of a batch run.

**Path params**:
- `batch_id` — the batch identifier

**Response** (`200 OK`):
```json
{
  "batch_id": "abc12345",
  "total_flows": 2,
  "completed": 1,
  "failed": 0,
  "running": 1,
  "pending": 0,
  "statuses": [
    {
      "run_id": "login",
      "status": "FINISHED",
      "goal": "Document login",
      "url": "https://app.example.com/login",
      "executed_steps": 5,
      "total_planned_steps": 5,
      "last_known_url": "https://app.example.com/login",
      "error": null
    }
  ]
}
```

**Error responses**:
- `404 Not Found` — batch does not exist

---

## Data Models

### RunRequest

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `run_id` | string | no | UUID | Alphanumeric + hyphens/underscores |
| `goal` | string | yes | — | Documentation goal |
| `url` | string | yes | — | Starting URL |
| `model` | string | no | `gpt-4o-mini` | LLM model for planning |
| `annotation_model` | string | no | null | Model for screenshot annotations |
| `language` | string | no | null | Output language (e.g. `pt`, `en`) |
| `title` | string | no | null | H1 title for the document |
| `headless` | boolean | no | true | Run Chrome headless |
| `cookies_file` | string | no | null | Path to session cookies JSON |
| `app_version` | string | no | null | Application version |
| `git_commit` | string | no | null | Git commit hash |
| `deployed_at` | string | no | null | ISO timestamp |

### RunStatusResponse

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | string | Run identifier |
| `status` | string | `PLANNING`, `EXECUTING`, `CRITIQUE`, `FINISHED`, `FAILED` |
| `goal` | string | Documentation goal |
| `url` | string | Starting URL |
| `executed_steps` | integer | Steps completed so far |
| `total_planned_steps` | integer | Total steps in current plan |
| `last_known_url` | string | Last URL the browser visited |
| `error` | string | Error message if failed |

### DeployWebhookPayload

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `app_version` | string | yes | Version being deployed |
| `git_commit` | string | yes | Commit hash |
| `deployed_at` | string | yes | ISO timestamp |
| `base_url` | string | yes | Application base URL |
| `flows` | FlowConfig[] | yes | Flows to document |
| `model` | string | no | `gpt-4o-mini` |
| `headless` | boolean | no | `true` |
| `cookies_file` | string | no | Path to cookies |

### FlowConfig

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `goal` | string | yes | Documentation goal |
| `path` | string | yes | URL path or full URL |
| `run_id` | string | no | Custom run identifier |
| `title` | string | no | Document title |
| `language` | string | no | Output language |

### BatchConfig

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `app_version` | string | no | null | Version |
| `git_commit` | string | no | null | Commit hash |
| `deployed_at` | string | no | null | ISO timestamp |
| `base_url` | string | no | null | Base URL for relative paths |
| `model` | string | no | `gpt-4o-mini` | LLM model |
| `headless` | boolean | no | `true` | Headless mode |
| `cookies_file` | string | no | null | Cookies path |
| `flows` | BatchConfigFlow[] | no | `[]` | Flow definitions |

---

## Health Check

### `GET /health`

Liveness probe.

**Response** (`200 OK`):
```json
{
  "status": "healthy",
  "service": "ready-ai",
  "version": "0.1.0",
  "timestamp": "2026-05-09T14:00:00Z"
}
```

---

### `GET /ready`

Readiness probe — checks the output directory is writable.

**Response** (`200 OK`):
```json
{
  "status": "ready",
  "checks": {
    "output_dir": "ok",
    "browser_pool": "ok"
  },
  "timestamp": "2026-05-09T14:00:00Z"
}
```

**Error responses**:
- `503 Service Unavailable` — output directory not writable

---

### `GET /`

Returns API info:
```json
{
  "title": "ready-ai API",
  "version": "0.6.1",
  "docs_url": "/docs"
}
```

> Note: FastAPI auto-generates OpenAPI docs at `/docs` and `/redoc`.

---

## Remote SDK mode

`ReadyAI` can talk to a running API server instead of driving the
engine locally. Pass `base_url` (and optionally `api_key`, sent as
`X-API-Key`):

```python
import asyncio
from ready_ai import ReadyAI

async def main():
    ai = ReadyAI(base_url="http://127.0.0.1:8000", api_key="secret")
    health = await ai.remote.health()    # GET /health -> RemoteHealth
    print(health.status, health.service)
    result = await ai.run(flow)          # POST /flows/run -> RunResult
    print(result.status)

asyncio.run(main())
```

Readers on `ai.remote` return typed models (`ready_ai.remote_models`,
`extra="ignore"` for forward compatibility); a shape the server
changed incompatibly raises `FlowValidationError` instead of
propagating half-parsed dicts. `get_metrics()` stays a loose mapping
(the metrics file has no schema contract) and `get_output()` returns
raw `bytes` (ZIP archive).

Available readers on `ai.remote` (all read-only):

| Method | Route |
|---|---|
| `health()` | `GET /health` |
| `ready()` | `GET /ready` |
| `get_runs()` | `GET /runs` |
| `get_run(run_id)` | `GET /runs/{run_id}` |
| `get_output(run_id)` | `GET /runs/{run_id}/output` |
| `get_metrics(run_id)` | `GET /runs/{run_id}/metrics` |
| `export_run(run_id)` | `POST /runs/{run_id}/export` (read-only effect) |
| `diff_run(run_id)` | `GET /runs/{run_id}/diff` |
| `list_doc_sets()` | `GET /doc-sets` |
| `doc_status(version)` | `GET /docs/{version}/status` |
| `get_history()` | `GET /history` |
| `get_aggregates()` | `GET /history/aggregates` |

Execution in remote mode: `run`, `run_flow` (and `run_sync`, which
delegates to `run`) execute via `POST /flows/run` and return a public
`RunResult` translated from the server's `FlowRunResult`. The public
flow is translated by whitelist (`name`, `url`, `steps`, `retries`,
`run_id`, `effect_policy` mapped to the server vocabulary, `headless`
from the call browser) — local profile references, output paths and
credentials never travel, and runs with a profile or `confirm` keys
fail closed with `PolicyViolationError` before any HTTP. `start`,
`resume` and `replay_manifest` have no server equivalent and stay
blocked in remote mode. Transport failures raise `ConnectionLostError`;
HTTP errors raise `ReadyAIError`.
