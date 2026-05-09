# Versioning Guide

Every documentation run is versioned alongside your application.

## Automatic Version Resolution

ready-ai resolves version metadata automatically from environment variables:

### Priority Order

1. **CLI args** — `--app-version`, `--git-commit`, `--deployed-at`
2. **Env vars** — `APP_VERSION`, `GITHUB_SHA`, `TAG_NAME`, `COMMIT_SHA`, `DEPLOYED_AT`
3. **Batch config** — `app_version`, `git_commit`, `deployed_at` fields
4. **Fallback** — `0.0.0` + current UTC timestamp

### Environment Variables

| Variable | Maps To | Fallback |
|----------|---------|----------|
| `APP_VERSION` | `app_version` | — |
| `TAG_NAME` | `app_version` | — |
| `GITHUB_SHA` | `git_commit` | — |
| `COMMIT_SHA` | `git_commit` | — |
| `DEPLOYED_AT` | `deployed_at` | current UTC time |

## Manifest File

Every run generates `manifest.json` alongside `docs.md`:

```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "goal": "Document the login page",
  "url": "https://app.example.com/login",
  "model": "gpt-4o-mini",
  "language": "en",
  "app_version": "2.3.1",
  "git_commit": "abc1234",
  "deployed_at": "2026-05-09T14:00:00Z",
  "generated_at": "2026-05-09T14:05:22Z",
  "steps_count": 7,
  "file_hashes": {
    "docs.md": "sha256:a1b2c3...",
    "screenshots/step_1.png": "sha256:d4e5f6..."
  }
}
```

## Output Directory Structure

```
output/
├── v2.3.1/
│   ├── login/
│   │   ├── docs.md
│   │   ├── manifest.json
│   │   └── screenshots/
│   └── onboarding/
│       ├── docs.md
│       ├── manifest.json
│       └── screenshots/
└── latest/  ← symlink to most recent version
```

## Diff Between Versions

Compare two documentation sets:

```python
from src.docs.text_diff import compare_docs

diff = compare_docs("output/v2.3.0/login/docs.md", "output/v2.3.1/login/docs.md")
diff.save_changelog("output/v2.3.1/login/changelog.md")
```

Produces `changelog.md`:

```markdown
# Documentation Changelog: v2.3.0 → v2.3.1

## Added Steps (2)
- Step 6: "Enable two-factor authentication"
- Step 7: "Verify backup codes"

## Removed Steps (1)
- Step 4: "Click 'Legacy Login' button"

## Modified Steps (3)
- Step 1: Annotation updated
- Step 3: Action changed from "click" to "hover"
- Step 5: Screenshot similarity: 0.72 → 0.95
```

## Tagging Strategy

Recommended git workflow:

```bash
# Tag a release
git tag -a v2.3.1 -m "Release v2.3.1"
git push origin v2.3.1

# The docs-generation workflow triggers automatically
# Docs are committed to branch: docs/v2.3.1
```

## Versioned Docs Branch

The CI workflow commits docs to a dedicated branch:

```
docs/v2.3.1/
├── login/
│   ├── docs.md
│   ├── manifest.json
│   └── screenshots/
└── onboarding/
    ├── docs.md
    ├── manifest.json
    └── screenshots/
```

This branch can be:
- Published to GitHub Pages
- Synced to Confluence/Notion
- Archived as release artifact

## Accessing Historical Docs

```bash
# List all doc versions
git branch -a | grep docs/

# Checkout a specific version
git checkout docs/v2.3.0

# Compare manifests
diff <(git show docs/v2.3.0:login/manifest.json) <(git show docs/v2.3.1:login/manifest.json)
```
