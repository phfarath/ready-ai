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

### `GET /`

Returns API info:
```json
{
  "title": "ready-ai API",
  "version": "0.2.0",
  "docs_url": "/docs"
}
```

> Note: FastAPI auto-generates OpenAPI docs at `/docs` and `/redoc`.
