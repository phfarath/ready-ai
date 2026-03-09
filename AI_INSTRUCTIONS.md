# AI Development Instructions — ready-ai

Guidelines for AI assistants working in this repository.

---

## 1. Mental Model

ready-ai is an agentic documentation engine that controls Chrome through raw CDP.

Core loop:

```text
goal + DOM -> Planner -> step list
step list  -> Executor -> screenshots + annotations
docs       -> Critic -> score + gaps -> re-execution
```

The repository now has two entry points:

- `ready-ai run` for local runs
- `ready-ai api` for the FastAPI server

Current product phase: **CLI hardening + API stabilization**.

---

## 2. Repository Layout

```text
main.py
src/
  agent/
    loop.py
    planner.py
    executor.py
    critic.py
    state.py
  cdp/
    connection.py
    browser.py
    page.py
    input.py
    runtime.py
  llm/
    client.py
    prompts.py
  docs/
    renderer.py
    output.py
  api/
    server.py
    manager.py
    models.py
tests/
  test_cdp_messages.py
  test_agent_loop_spa_drift.py
  test_api.py
  test_api_requests.py
```

Read `src/agent/loop.py` first when you need to understand end-to-end behavior.

---

## 3. Architecture Invariants

### 3.1 No browser automation framework

All browser control must go through `src/cdp/`. Do not introduce Playwright, Selenium, Puppeteer, or WebDriver abstractions into the product code.

### 3.2 Agent boundaries stay explicit

- `planner.py` plans
- `executor.py` executes one step
- `critic.py` reviews output
- `loop.py` orchestrates

If a feature crosses boundaries, wire it through `loop.py` or add a helper module.

### 3.3 LLM calls go through `LLMClient`

Do not call `litellm` directly outside `src/llm/client.py`.

### 3.4 `DocRenderer.render()` stays pure

No network calls, no file writes, no LLM calls inside the renderer.

### 3.5 Raw CDP is a design choice

Do not replace the CDP layer with a higher-level browser framework for convenience.

---

## 4. Key Product Behaviors

- checkpoint state is persisted and may be reused by the API
- annotations may use a separate model from planning
- `title` is display text; `goal` is the LLM instruction
- `language` is optional; renderer labels fall back to English when omitted
- same-origin iframes and shadow DOM are supported; multi-tab and cross-origin flows are not fully solved yet

---

## 5. Feature Change Checklist

### Adding a CLI flag

1. Update argument parsing in `main.py`.
2. Thread the value into `AgenticLoop`.
3. Store it on the loop or downstream object.
4. Update `README.md` and `FEATURES.md`.
5. If it changes roadmap or scope, update `PRD.md` and `NEXT_STEPS.md`.

### Adding a CDP action

1. Implement it in `executor.py`.
2. Document it in the executor prompts.
3. Add a regression test.

### Adding API behavior

1. Update `src/api/models.py` if request or response contracts change.
2. Update `src/api/server.py` and `src/api/manager.py`.
3. Add or update API tests.
4. Update `README.md`, `FEATURES.md`, and `PRD.md` if the product surface changed.

### Adding renderer behavior

1. Keep the change inside `src/docs/renderer.py` unless file output semantics also change.
2. Add a renderer-focused test.

---

## 6. Testing

Preferred command:

```bash
python3 -m pytest -q
```

Expectations:

- run tests before committing
- add regression coverage for bug fixes
- avoid real Chrome or real LLM calls in unit tests

Do not hardcode historical test counts in docs; the suite will evolve.

---

## 7. Common Pitfalls

- Escape user-controlled strings passed into JS with `json.dumps()`.
- Avoid blocking I/O in async paths.
- Preserve event re-queue behavior in CDP event waiting logic.
- Do not conflate `title` with `goal`.
- Do not document a roadmap item as future if it already exists in code.

---

## 8. Documentation Hygiene

When you change product behavior, keep these files aligned:

- `README.md` for usage and installation
- `FEATURES.md` for capability inventory
- `PRD.md` for phase and roadmap
- `NEXT_STEPS.md` for the active delivery sequence

If the repository state and docs disagree, update the docs in the same task.

---

## 9. Before Opening a PR

- run `python3 -m pytest -q`
- update docs if CLI/API behavior changed
- avoid `print()` in product code
- avoid direct `litellm` imports outside `src/llm/client.py`
- keep raw CDP as the only browser control path
