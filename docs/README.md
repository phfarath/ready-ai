# ready-ai Documentation

Complete documentation for the ready-ai self-healing documentation pipeline.

## Quick Links

| Guide | Purpose |
|-------|---------|
| [API Reference](API.md) | Complete REST API documentation |
| [Batch Runner](BATCH.md) | YAML/TOML batch configuration guide |
| [Webhook Integration](WEBHOOK.md) | Deploy webhook setup and examples |
| [CI/CD Guide](CI-CD.md) | GitHub Actions, Docker, regression tests |
| [Versioning](VERSIONING.md) | How docs are versioned with your app |
| [Notifications](NOTIFICATIONS.md) | Webhook notifications for doc events |

## Overview

ready-ai is an agentic browser automation tool that:
1. **Drives Chrome** over raw CDP (Chrome DevTools Protocol)
2. **Plans flows** from DOM snapshots using LLMs
3. **Executes actions**, captures screenshots, and generates annotations
4. **Writes Markdown** step-by-step documentation
5. **Tests documentation** against live UI with visual diff
6. **Auto-heals** docs when drift is detected
7. **Versions** docs with your app releases
8. **Notifies** your team when docs change

## Architecture

```text
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Deploy    │────▶│  Webhook    │────▶│   Batch     │
│   Event     │     │   /deploy   │     │   Runner    │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
                       ┌─────────────────────────┘
                       ▼
              ┌─────────────────┐
              │  AgenticLoop    │
              │  (per flow)     │
              │                 │
              │  planner ──▶ executor ──▶ critic
              │     │            │           │
              │     ▼            ▼           ▼
              │  steps      screenshots  re-plan
              │     │            │
              │     └──────▶   docs.md
              │                manifest.json
              └─────────────────┘
                       │
                       ▼
              ┌─────────────────┐
              │   DocTestRunner │
              │   (re-execute)  │
              │                 │
              │  visual diff    │
              │  auto-heal      │
              │  notify         │
              └─────────────────┘
```

## Core Concepts

### Documentation Run
A single session that documents one user flow. Produces `docs.md` + `manifest.json` + screenshots.

### Batch
A collection of runs triggered by a single deploy event or config file. All flows share the same `app_version` and `git_commit`.

### Self-Healing
When the live UI changes, ready-ai detects drift, re-executes steps, updates screenshots, and generates a changelog.

### Manifest
JSON file alongside each `docs.md` with metadata: version, commit, timestamps, step hashes, file checksums.
