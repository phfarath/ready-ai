# AI Development Instructions — browser-auto

Guidelines for AI assistants (Claude Code, Cursor, Copilot, etc.) working on this codebase. Read this before making any changes.

---

## 1. Mental Model

browser-auto is a **three-agent documentation pipeline** that controls a real Chrome browser via raw CDP WebSocket. The core loop is:

```
goal + DOM  →  Planner (LLM)  →  step list
step list   →  Executor (CDP + LLM)  →  screenshots + annotations
docs        →  Critic (LLM)  →  score + gaps  →  loop back
```

Every significant behavior lives in one of three places:
- **`src/agent/`** — agent logic (planning, execution, criticism, orchestration)
- **`src/cdp/`** — raw browser control (no framework abstractions, pure CDP JSON-RPC)
- **`src/llm/`** — LLM client + prompt templates

The `src/docs/` layer is pure rendering — no LLM calls, no CDP calls.

---

## 2. Repository Layout

```
main.py                  CLI entry point (argparse → AgenticLoop)
src/
  agent/
    loop.py              Main orchestrator — READ THIS FIRST
    planner.py           Goal + DOM → numbered step list
    executor.py          Step → CDP action → StepResult
    critic.py            Docs → CriticFeedback (score, missing_steps)
  cdp/
    connection.py        Raw WebSocket JSON-RPC (CDPConnection)
    browser.py           Chrome launcher + WS URL fetcher
    page.py              Page domain (navigate, screenshot, DOM)
    input.py             Input domain (click, type, scroll)
    runtime.py           Runtime domain (JS eval, interactive elements)
  llm/
    client.py            Async LiteLLM wrapper (complete, complete_with_vision)
    prompts.py           All 7 prompt templates — EDIT WITH CARE
  docs/
    renderer.py          DocRenderer — Markdown builder
    output.py            File I/O (save docs + screenshots)
tests/
  test_cdp_messages.py   Unit tests — must pass before every commit
```

---

## 3. Architecture Invariants — Never Break These

### 3.1 No browser automation framework
All browser control MUST go through `src/cdp/`. Never import or add Playwright, Selenium, Puppeteer, or any WebDriver. The CDP layer is intentionally raw; this is a design choice, not an oversight.

### 3.2 Agent roles are separated
- `planner.py` only produces step lists — no CDP calls, no screenshots.
- `executor.py` only executes one step at a time — no planning, no critic logic.
- `critic.py` only reviews markdown — no CDP calls.
- `loop.py` is the only place that wires them together.

Do not cross these boundaries. If you need new cross-cutting behavior, add it to `loop.py` or extract a new helper module.

### 3.3 LLM calls only through LLMClient
Never call `litellm` directly from agent modules. Always use `LLMClient.complete()` or `LLMClient.complete_with_vision()`. This ensures rate-limit retry, model routing, and consistent logging.

### 3.4 Annotator prompt must receive both `{goal}` and `{step}`
`ANNOTATOR_PROMPT.format(goal=..., step=...)` — both placeholders are required. The goal anchors the output language; the step provides the context. Passing only `step=` will raise a `KeyError`.

### 3.5 DocRenderer is pure — no side effects in render()
`DocRenderer.render()` must remain a pure function: same inputs → same output, no I/O, no LLM calls. File writing happens only in `save_docs()`.

---

## 4. Coding Conventions

### Python style
- Python 3.10+ — use `match`, `|` union types, structural pattern matching where appropriate.
- Type hints on all public functions and class methods.
- `async`/`await` throughout — this is an async codebase; no blocking I/O in async contexts.
- Dataclasses for structured data (`StepResult`, `DocStep`, `CriticFeedback`) — not dicts.

### Naming
- CDP methods: `PascalCase.camelCase` (e.g., `Page.captureScreenshot`) — match the CDP spec exactly.
- Agent functions: `snake_case` verbs (`plan`, `execute_step`, `review`).
- Private methods on classes: prefix with `_` (`_execute_steps`, `_critic_loop`).

### Logging
- Use `logger = logging.getLogger(__name__)` at module level.
- `logger.info` for pipeline milestones (step start, score, save).
- `logger.debug` for verbose detail (DOM size, selector choices).
- `logger.warning` for recoverable issues (timeout, fallback used).
- `logger.error` for unrecoverable failures only.
- Never use `print()`.

### Prompt templates (`src/llm/prompts.py`)
- All prompts are module-level string constants in `UPPER_SNAKE_CASE`.
- Placeholders use `{name}` format (Python `.format()` style).
- Document which placeholders are required in a comment above each prompt.
- When adding a new prompt: add it to `src/llm/__init__.py` exports.

---

## 5. How to Add a Feature

### Adding a new CLI flag
1. Add `parser.add_argument(...)` in `main.py:parse_args()`.
2. Pass it to `AgenticLoop(...)` in `main.py:async_main()`.
3. Add the parameter to `AgenticLoop.__init__` with a default.
4. Store as `self.<name>`.
5. Thread it to wherever it's needed (renderer, executor, etc.).
6. Update the Options table in `README.md` and the CLI section in `FEATURES.md`.

### Adding a new CDP action type
1. Add the action handler in `executor.py:_dispatch_action()`.
2. Document it in `EXECUTOR_SYSTEM` prompt under the numbered action list.
3. Also add it to `EXECUTOR_RETRY_SYSTEM` if it's relevant for retries.
4. Add a test case in `tests/test_cdp_messages.py`.

### Adding a new agent prompt
1. Define the constant in `prompts.py` with a comment listing its placeholders.
2. Export it from `src/llm/__init__.py`.
3. Import it where needed (only from `agent/` modules).

### Adding a new DocRenderer feature
1. Modify `renderer.py` only — no changes to `output.py` unless file I/O changes.
2. `render()` must remain a pure function.
3. Add/update the `TestDocRenderer` test class.

---

## 6. Testing

```bash
python3 -m pytest --tb=short -q
```

**All 27 tests must pass before committing.** Do not commit if tests are red.

### Test file: `tests/test_cdp_messages.py`
Covers: CDPConnection message structure, DocRenderer rendering, planner step parsing, executor JSON parsing, wait_for_event buffering, DOM change detection, selector extraction, credential escaping.

### When to add tests
- Every new public function in `src/` should have at least one test.
- Every bug fix should have a regression test.
- Tests are unit tests — no real Chrome, no real LLM calls, no network. Use mocks or test pure logic only.

### What NOT to test
- The LLM response quality — test the parsing/handling of responses, not the LLM.
- The CDP protocol itself — Chrome implements it, not us.

---

## 7. Common Pitfalls

### JS injection — always escape user-supplied strings
Any string from user input (credentials, selectors, text to find) injected into a `runtime.evaluate()` call MUST be escaped with `json.dumps()`. Raw f-string interpolation is a security vulnerability (XSS/injection). See `_handle_login` for the correct pattern.

### Async sleep — minimize, don't eliminate
`asyncio.sleep(0.5)` settle waits exist because CDP doesn't reliably signal UI stability. Do not remove them. Do not increase them unnecessarily. The right fix is `Page.lifecycleEvent` detection (see roadmap).

### wait_for_event — always re-queue stashed events
`CDPConnection.wait_for_event` buffers non-matching events and re-queues them in a `try/finally`. Never rewrite this logic without preserving that behaviour — dropping events causes silent deadlocks in SPAs.

### get_interactive_elements — 60 element limit
The JS traversal caps at 60 elements to stay within LLM context limits. If you increase this, also increase the DOM truncation limit in planner calls, or the LLM context will overflow.

### DocRenderer title vs goal
`self.title or self.goal` — `title` is the display heading, `goal` is the LLM instruction. Never conflate them. Both must always be available on the renderer.

---

## 8. What NOT to Do

| Don't | Do instead |
|-------|-----------|
| Import `playwright`, `selenium`, `pyppeteer` | Use `src/cdp/` directly |
| Call `litellm.acompletion()` directly | Use `LLMClient.complete()` or `complete_with_vision()` |
| Add `time.sleep()` | Use `await asyncio.sleep()` |
| Use `print()` for output | Use `logger.*` |
| Hardcode model names | Pass through `--model` / `--annotation-model` |
| Put LLM calls in `DocRenderer` | Rendering is pure; LLM calls go in `loop.py` |
| Store screenshots as inline base64 in Markdown | Write files via `save_docs()`; reference as relative paths |
| Commit without running tests | Run `python3 -m pytest -q` first |
| Amend a published commit | Create a new commit |
| Add complexity for hypothetical future use | YAGNI — build for the current requirement |

---

## 9. Key Design Decisions (Why Things Are the Way They Are)

**Why raw CDP instead of Playwright?**
No subprocess overhead, no API surface to depend on, and direct access to every CDP domain (Network, Target, etc.) without abstraction leakage. The CDPConnection is intentionally minimal.

**Why LiteLLM?**
Single call site, one dependency, provider-agnostic. Swap OpenAI for Anthropic by changing a CLI flag. No vendor lock-in.

**Why annotate per-step with vision instead of at the end?**
Context is richest immediately after the action, when the screenshot reflects exactly what the executor just did. Annotating at the end would require re-feeding all screenshots, multiplying cost.

**Why anchor annotation language to `{goal}` not `{step}`?**
The goal is user-controlled and stable. Step descriptions can pick up words from the UI's own language (button labels, page titles), causing the annotation LLM to drift into the UI's language. The goal is the single source of truth for intended output language.

**Why `--title` separate from `--goal`?**
Goals are instruction strings — verbose and task-oriented ("Document all features of X: navigation, pages..."). Titles are headings — short and readable ("X — User Guide"). Conflating them forces users to write awkward H1s or weak goals.

---

## 10. Before Opening a PR

- [ ] `python3 -m pytest -q` — all tests pass
- [ ] `README.md` Options table updated if CLI flags changed
- [ ] `FEATURES.md` updated if a new feature was added
- [ ] `NEXT_STEPS.md` updated — mark completed items ✅, add new open items
- [ ] No `print()` statements
- [ ] No new direct `litellm` imports outside `src/llm/client.py`
- [ ] No new `time.sleep()` (async codebase — use `asyncio.sleep`)
- [ ] Commit message follows `type(scope): description` convention (see git log)
