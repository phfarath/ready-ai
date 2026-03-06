# Open Source Status

**Last updated:** 2026-03-06
**Repository:** `ready-ai`

## Current State

The repository is now in good shape to exist publicly as the open-source core of browser-auto.

Completed:

- standalone repository extracted from the old monorepo
- Git history rewritten to remove machine-local author email
- empty Zenflow initial commit removed from history
- `LICENSE`, `CONTRIBUTING.md`, and `SECURITY.md` added
- README updated for open-source positioning
- `.gitignore` updated for local artifact hygiene
- no `.env` file tracked
- tracked files reviewed and limited to source, tests, and docs

## Verified Checks

- `git status` is clean
- `git ls-files` does not include `.env`, `output/`, or local run artifacts
- `python3 main.py --help` works and shows the documented `run` and `api` commands
- repository remote points to `https://github.com/phfarath/ready-ai.git`
- `python3 -m pytest -q` passes locally

## Remaining Blockers

### 1. API install story is still inconsistent

The repository currently uses both:

- `pyproject.toml` for core and dev dependencies
- `requirements.txt` for API-related extras

This is workable, but not yet polished for outside contributors.

### 2. Public release polish can still improve

Not blocking, but still useful:

- add GitHub repository description and topics
- optionally add issue templates
- optionally add a small roadmap section to the GitHub repo description

## Recommended Next Actions

1. Make the installation path consistent for contributors.
2. Review the GitHub repo settings, description, and visibility.
3. Optionally add issue templates for public triage.

## Assessment

This repository is now:

- **safe enough to exist publicly as code**
- **functionally healthy enough for outside testing**
- **not yet fully polished as a contributor-ready project**
