# Open Source Status

**Last updated:** 2026-03-06
**Repository:** `ready-ai`

## Current State

The repository is now in good shape to exist publicly as ready-ai.

Completed:

- standalone repository extracted from the old monorepo
- Git history rewritten to remove machine-local author email
- empty Zenflow initial commit removed from history
- `LICENSE`, `CONTRIBUTING.md`, and `SECURITY.md` added
- README updated for open-source positioning
- GitHub repository description and topics reviewed
- `.gitignore` updated for local artifact hygiene
- issue templates added for public triage
- minimal GitHub Actions test workflow added
- no `.env` file tracked
- tracked files reviewed and limited to source, tests, and docs

## Verified Checks

- `git ls-files` does not include `.env`, `output/`, or local run artifacts
- `python3 main.py --help` works and shows the documented `run` and `api` commands
- repository remote points to `https://github.com/phfarath/ready-ai.git`
- `python3 -m pytest -q` passes locally

## Remaining Blockers

### 1. Installation UX can still improve

The repository now has one canonical local install path:

- `pip install -e ".[dev]"`

`requirements.txt` remains only as a thin wrapper for tooling that expects it.

This is substantially clearer, but can still improve with more explicit environment notes for contributors.

### 2. Contributor setup can still be smoother

Not blocking, but still useful:

- validate the GitHub Actions workflow on the public repository
- optionally document a single canonical install path for CLI + API + tests
- optionally add a small roadmap section to the GitHub repo description

## Recommended Next Actions

1. Make the installation path consistent for contributors.
2. Validate the new issue templates and CI workflow after the next push.
3. Run pilot validation on a few real SaaS flows before expanding scope.

## Assessment

This repository is now:

- **safe enough to exist publicly as code**
- **functionally healthy enough for outside testing**
- **materially closer to contributor-ready, with a few onboarding gaps left**
