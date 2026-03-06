# Open Source Release Checklist

Use this checklist before making the repository public.

## Secrets and Sensitive Data

- [ ] Confirm `.env` is not tracked
- [ ] Rotate any real API keys used during development
- [ ] Confirm no cookie exports or session files are tracked
- [ ] Confirm no generated outputs in `output/` are tracked
- [ ] Confirm no local screenshots, traces, or customer data are tracked

## Repository Hygiene

- [ ] Review `git ls-files` for accidental local artifacts
- [ ] Verify `.gitignore` covers local environment files and generated outputs
- [ ] Ensure examples use placeholder values only
- [ ] Ensure docs do not reference private infrastructure or internal-only paths

## Open Source Basics

- [ ] License is present
- [ ] `README.md` explains what the project is and how to run it
- [ ] `CONTRIBUTING.md` exists
- [ ] `SECURITY.md` exists
- [ ] MVP and roadmap docs reflect the intended public story

## GitHub Repository Page

- [ ] Set GitHub description to: `Open-source core for agentic browser automation that generates step-by-step SaaS documentation with screenshots.`
- [ ] Add GitHub topics: `browser-automation`, `chrome-devtools-protocol`, `cdp`, `agentic-ai`, `llm`, `documentation`, `screenshots`, `markdown`, `fastapi`, `saas`
- [ ] Keep the homepage field empty until there is a real public landing page
- [ ] Verify the README first screen states that this repo is the open-source core
- [ ] Enable `Issues` only if incoming triage will actually be monitored
- [ ] Leave `Wiki`, `Projects`, and `Discussions` off unless they will be actively maintained
- [ ] Add a social preview image only after there is intentional branding
- [ ] Switch visibility to `Public` only after the About box and README are aligned

## Product Positioning

- [ ] Position the repo as the open-source core, not the full future platform
- [ ] Keep launch messaging focused on core SaaS documentation flows
- [ ] Avoid overpromising unsupported flows such as MFA, multi-tab, and cross-origin iframe support

## Validation

- [ ] `python3 -m pytest -q` has been run
- [ ] CLI help matches the README examples
- [ ] At least one clean local install path has been verified
- [ ] The public-facing story matches the current product reality

## Final Push Check

- [ ] Review `git diff --cached`
- [ ] Review `git status`
- [ ] Confirm no secret files are staged
- [ ] Confirm the first public commit contains the intended docs and governance files only
