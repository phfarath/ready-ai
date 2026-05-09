# 🛣️ Self-Healing Documentation for Releases — Roadmap

**Goal:** Transform `ready-ai` from a manual CLI tool into an autonomous self-healing documentation pipeline that detects new releases, regenerates docs, and notifies the team.

---

## Phase 1: Foundation (Week 1–2)
> **Branch:** `feat/self-healing-releases-phase-1`

### 1.1 Document Versioning
- Add metadata to every generated `docs.md`:
  - `app_version` — version string from deploy payload or env
  - `deployed_at` — ISO timestamp of when this version went live
  - `git_commit` — commit hash from source repo
  - `run_id` — unique identifier for this doc generation run
- Version the output directory: `./output/{app_version}/{run_id}/`
- Save a `manifest.json` per run with all metadata + list of generated files

### 1.2 Textual Diff Engine
- New module: `src/docs/text_diff.py`
- Compare two `docs.md` files and produce a structured diff:
  - Added steps
  - Removed steps
  - Modified annotations
  - Modified action descriptions
  - Screenshot changes (via visual diff score)
- Output formats:
  - `changelog.md` — human-readable Markdown
  - `diff.json` — machine-readable for CI/API consumption
- Integrate into auto-healer: show "what changed in the text" alongside visual drift

### 1.3 Notification System
- New module: `src/notify.py`
- Webhook-based notifications (non-blocking, fire-and-forget)
- Supported channels (config via `.env`):
  - Slack (`SLACK_WEBHOOK_URL`)
  - Discord (`DISCORD_WEBHOOK_URL`)
  - Telegram (usamos nós mesmos!)
  - Generic HTTP webhook (`NOTIFY_WEBHOOK_URL`)
- Events that trigger notifications:
  - `DOC_GENERATED` — new documentation created
  - `DRIFT_DETECTED` — UI changed, docs need healing
  - `AUTO_HEALED` — docs were automatically fixed
  - `DOC_BROKEN` — step execution failed, manual intervention needed
  - `TEST_PASSED` — all tests passed, docs are up to date

---

## Phase 2: Deploy Automation (Week 3–4)
> **Branch:** `feat/self-healing-releases-phase-2`

### 2.1 Deploy Webhook Endpoint
- Expand FastAPI API with `POST /webhooks/deploy`
- Payload schema:
  ```json
  {
    "app_version": "2.3.1",
    "git_commit": "abc123",
    "deployed_at": "2026-05-09T14:00:00Z",
    "base_url": "https://app.example.com",
    "flows": [
      {"goal": "Document login", "path": "/login", "run_id": "login"},
      {"goal": "Document onboarding", "path": "/welcome", "run_id": "onboarding"}
    ]
  }
  ```
- On receive: queue and execute `ready-ai run` for each flow
- Return `202 Accepted` with `batch_id` for polling status

### 2.2 Batch / Multi-Flow Configuration
- Extend config file format to support multiple flows:
  ```yaml
  # ready-ai.yaml
  app_version: "2.3.1"
  base_url: "https://app.example.com"
  
  flows:
    - goal: "Document login flow"
      path: "/login"
      run_id: "login-v2.3.1"
      output: "./output/v2.3.1/login"
    - goal: "Document onboarding"
      path: "/welcome"
      run_id: "onboarding-v2.3.1"
      output: "./output/v2.3.1/onboarding"
  ```
- New CLI flag: `ready-ai run --config ready-ai.yaml --batch`
- Sequential execution (parallel in Phase 3)

### 2.3 GitHub Action Reusable Workflow
- New directory: `.github/actions/ready-ai/`
- `action.yml` accepting inputs:
  - `goal`, `url`, `config-file`, `api-key`
- Runs `ready-ai run` or `ready-ai test` with headless Chrome
- Publishes docs as artifact or commits to `docs/` branch

---

## Phase 3: CI/CD & Monitoring (Week 5–6)
> **Branch:** `feat/self-healing-releases-phase-3`

### 3.1 CI Pipeline Integration
- New workflow: `.github/workflows/docs-generation.yml`
  - Trigger: on release/tag push or manual dispatch
  - Runs `ready-ai run` for all configured flows
  - Commits generated docs to `docs/{version}/` branch
- New workflow: `.github/workflows/docs-regression.yml`
  - Trigger: on PRs touching frontend code
  - Runs `ready-ai test` against staging URL
  - Fails PR if docs drift beyond threshold

### 3.2 Docker Support for CI
- `Dockerfile` with Chrome headless + Python
- Multi-stage build for small image size
- Published to GHCR for CI consumption

### 3.3 API Expansion (Dashboard Data)
- `GET /runs` — list all documentation runs
- `GET /runs/{run_id}` — detailed run status + results
- `GET /runs/{run_id}/diff` — textual diff from previous version
- `GET /docs` — list all generated documentation sets
- `GET /docs/{version}/status` — health of docs for a version

### 3.4 Historical Tracking
- SQLite or JSON file-based history store
- Track per-version metrics:
  - Steps count, pass rate, drift count
  - LLM tokens consumed, execution time
  - Auto-heal success rate

---

## Phase 4: Advanced Features (Future)
> **Branch:** `feat/self-healing-releases-phase-4` (optional)

- **Parallel Flow Execution** — run multiple flows simultaneously
- **Confluence/Notion Integration** — push docs directly to wiki
- **Notifications (Telegram)** — notify team about drift/regeneration
- **Screenshot Archive** — S3/R2 bucket for long-term screenshot storage
- **Team Dashboard** — simple web UI showing all docs status
- **Selector Health Score** — track which selectors break most often
- **A/B Test Support** — document multiple UI variants

---

## Branch Strategy

```
main
├── feat/self-healing-releases-phase-1  ← versioning + diff + notify
│       └── PR #1
├── feat/self-healing-releases-phase-2  ← webhook + batch + action
│       └── PR #2
├── feat/self-healing-releases-phase-3  ← CI + docker + API + history
│       └── PR #3
└── feat/self-healing-releases-phase-4  ← advanced/future
        └── PR #4 (optional)
```

Each phase is independent and reviewable. Merge `phase-1` → `main` before opening `phase-2`.

---

## Success Criteria

1. ✅ After a deploy, docs are regenerated automatically (or with a single webhook call)
2. ✅ Team is notified when docs drift or are regenerated
3. ✅ Changelog/diff is available between versions
4. ✅ CI blocks PRs that break documented flows
5. ✅ All existing tests continue to pass (`pytest -q`)
