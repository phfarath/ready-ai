# Contributing

Thanks for contributing to browser-auto.

## Before You Start

- Read [README.md](README.md) for product context and setup.
- Read [AI_INSTRUCTIONS.md](AI_INSTRUCTIONS.md) for architecture and maintenance rules.
- Keep changes focused. Small, reviewable pull requests are preferred.

## Development Setup

Install the project for local development:

```bash
pip install -e ".[dev]"
```

`requirements.txt` is kept only as a thin wrapper for tools that expect that filename and resolves to the same dependency set.

Set at least one LLM provider key in your local environment:

```bash
export OPENAI_API_KEY="your-key-here"
```

Do not commit secrets, cookies, generated outputs, or local environment files.

## Branching and Pull Requests

- Create a branch for your change.
- Use clear commit messages.
- Open a pull request with a concise description of the problem and the fix.
- Link related issues when relevant.

## What to Update With Code Changes

If your change affects product behavior, also update the relevant docs:

- [README.md](README.md) for setup and usage
- [FEATURES.md](FEATURES.md) for capability inventory
- [PRD.md](PRD.md) for phase and roadmap changes
- [NEXT_STEPS.md](NEXT_STEPS.md) for active execution priorities

## Testing

Run the test suite before opening a pull request:

```bash
python3 -m pytest -q
```

If you fix a bug, add a regression test when practical.

## Code Guidelines

- Keep browser control inside `src/cdp/`.
- Do not introduce Playwright, Selenium, or similar frameworks into product code.
- Route model calls through `src/llm/client.py`.
- Keep `DocRenderer.render()` free of side effects.
- Escape user-controlled strings passed into JS with `json.dumps()`.

## Pull Request Checklist

- Tests run locally
- No secrets or local artifacts added
- Docs updated if behavior changed
- Scope is limited to the stated change

## Reporting Problems

- Use GitHub Issues for bugs, ideas, and feature requests.
- For security-sensitive reports, follow [SECURITY.md](SECURITY.md) and do not open a public issue.
