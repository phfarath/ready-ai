# Test & Runtime Audit Report — ready-ai

**Date:** 2026-07-11
**Project:** ready-ai @ `C:\Dev\ready-ai`
**Python:** 3.12.4
**Platform:** Windows (win32 10.0.26200)

---

## 1. Python Environment & Installed Packages

**Python version:** Python 3.12.4

**Project installation:** Editable install confirmed — `python -c "import src; print('ok')"` → `ok`
Editable project location: `C:\Dev\ready-ai` (package `ready-ai` 0.1.0)

**Key packages (relevant to task):**

| Package | Version |
|---|---|
| pytest | 9.0.3 |
| pytest-asyncio | 1.3.0 |
| pytest-cov | 7.1.0 |
| pytest-mock | 3.15.1 |
| pytest-xdist | 3.8.0 |
| ruff | 0.15.12 |
| fastapi | 0.136.1 |
| uvicorn | 0.46.0 |
| httpx | 0.28.1 |
| litellm | 1.83.14 |
| websockets | 15.0.1 |
| coverage | 7.13.5 |
| click | 8.1.8 |
| typer | 0.23.1 |
| python-dotenv | 1.2.2 |

**Result:** All required dependencies are already installed. No `pip install -e ".[dev]"` was needed.

---

## 2. Lint Results

**Command:** `ruff check src/ tests/ main.py`

**Result:** ✅ **All checks passed!** (exit code 0)

No warnings, no errors. The codebase is fully lint-clean.

---

## 3. Test Results

**Command:** `python -m pytest -q --tb=short`

**Result:** ✅ **365 passed, 2 warnings** in 62.41s (exit code 0)

**Warnings (2):**

Both warnings originate from the same source and are identical `RuntimeWarning` instances:

```
tests/test_e2e_doc_test.py::TestE2EDriftDetected::test_drift_detected
tests/test_e2e_doc_test.py::TestE2EReportStructure::test_report_json_structure
  C:\Dev\ready-ai\src\agent\test_runner.py:206: RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited
    result = await self._test_step(
  Enable tracemalloc to get traceback where the object was allocated.
```

**Interpretation:** A mock object configured with `AsyncMock` is being called as if it were a synchronous mock in `src/agent/test_runner.py:206`, leaving a coroutine object un-awaited. This is a test-code hygiene issue (not a functional failure) — the tests still pass, but an unnecessary coroutine object is created and discarded without being awaited, which can mask bugs and produces noise in test output.

**No test failures.**

---

## 4. Coverage Summary

**Command:** `python -m pytest -q --cov=src --cov-report=term-missing`

**Overall coverage:** **61%** (4486 statements, 1742 missed)

All 365 tests passed during the coverage run as well.

### Coverage per module (sorted lowest first):

| Module | Stmts | Miss | Cover | Notes |
|---|---|---|---|---|
| `src\docs\text_diff.py` | 126 | 126 | **0%** | ⚠️ Completely untested |
| `src\cdp\input.py` | 69 | 55 | **20%** | Low coverage |
| `src\agent\recovery.py` | 99 | 78 | **21%** | Low coverage |
| `src\agent\executor.py` | 189 | 146 | **23%** | Low coverage |
| `src\api\batch_loader.py` | 40 | 28 | **30%** | Low coverage |
| `src\docs\auto_healer.py` | 106 | 71 | **33%** | Low coverage |
| `src\llm\client.py` | 94 | 61 | **35%** | Low coverage |
| `src\agent\browser_session.py` | 193 | 115 | **40%** | Low coverage |
| `src\api\manager.py` | 124 | 72 | **42%** | Low coverage |
| `src\api\server.py` | 369 | 206 | **44%** | Low coverage |
| `src\cdp\page.py` | 206 | 108 | **48%** | Low coverage |
| `src\mcp_server.py` | 77 | 40 | **48%** | Low coverage |
| `src\docs\output.py` | 26 | 21 | **19%** | Low coverage |
| `src\history.py` | 71 | 35 | **51%** | Moderate |
| `src\notify.py` | 64 | 29 | **55%** | Moderate |
| `src\agent\loop.py` | 290 | 114 | **61%** | Moderate |
| `src\cdp\browser.py` | 84 | 30 | **64%** | Moderate |
| `src\cdp\runtime.py` | 76 | 26 | **66%** | Moderate |
| `src\agent\cursor.py` | 79 | 27 | **66%** | Moderate |
| `src\observability.py` | 188 | 64 | **66%** | Moderate |
| `src\cdp\connection.py` | 308 | 61 | **80%** | Good |
| `src\docs\semantic_diff.py` | 17 | 3 | **82%** | Good |
| `src\versioning.py` | 45 | 7 | **84%** | Good |
| `src\docs\parser.py` | 55 | 7 | **87%** | Good |
| `src\docs\healing_publisher.py` | 172 | 14 | **92%** | Good |
| `src\docs\terminal_output.py` | 72 | 4 | **94%** | Good |
| `src\agent\state.py` | 54 | 2 | **96%** | Good |
| `src\cdp\accessibility.py` | 72 | 3 | **96%** | Good |
| `src\cdp\sanitize.py` | 138 | 4 | **97%** | Good |
| `src\agent\planner.py` | 29 | 1 | **97%** | Good |
| `src\docs\report_html.py` | 49 | 1 | **98%** | Good |
| `src\docs\visual_diff.py` | 50 | 1 | **98%** | Good |
| `src\agent\dom_utils.py` | 17 | 0 | **100%** | Full |
| `src\api\models.py` | 120 | 0 | **100%** | Full |
| `src\docs\manifest.py` | 39 | 0 | **100%** | Full |
| `src\llm\prompts.py` | 10 | 0 | **100%** | Full |
| `src\cdp\connection_state.py` | 17 | 0 | **100%** | Full |
| `src\cdp\exceptions.py` | 2 | 0 | **100%** | Full |

### Coverage gaps worth noting:
- **`src/docs/text_diff.py` (0%)** — 126 statements, completely untested. This is the most significant gap.
- **`src/agent/executor.py` (23%)** — 146/189 statements missed; core execution path is largely uncovered.
- **`src/agent/recovery.py` (21%)** — 78/99 statements missed; recovery logic is largely untested.
- **`src/cdp/input.py` (20%)** — 55/69 statements missed; CDP input simulation is largely untested.
- **`src/llm/client.py` (35%)** — 61/94 statements missed; LLM client is partially tested.

---

## 5. CLI Help Verification

**Command structure:** `main.py` uses argparse with 5 subcommands: `run`, `test`, `api`, `batch`, `export`.

### `python main.py --help` ✅
```
usage: main.py [-h] {run,test,api,batch,export} ...

🤖 ready-ai: Agentic browser automation for SaaS documentation

positional arguments:
  {run,test,api,batch,export}
                        Sub-commands
    run                 Run the documentation agent locally
    test                Test documentation against live UI (self-healing)
    api                 Start the FastAPI server
    batch               Run multiple documentation flows from a config file
    export              Export generated docs to a static-site format
```
All 5 subcommands listed correctly. Exit code 0.

### `python main.py run --help` ✅
Lists all options: `--goal/-g`, `--url/-u`, `--title/-t`, `--language/-l`, `--model/-m`, `--output/-o`, `--port/-p`, `--headless`, `--max-critic-rounds`, `--annotation-model`, `--cookies-file`, `--username`, `--password`, `--verbose/-v`, `--config`, `--run-id`, `--resume`, `--plan-only`, `--app-version`, `--git-commit`, `--deployed-at`. Exit code 0.

### `python main.py test --help` ✅
Lists all options: `--doc/-d` (required), `--url/-u` (required), `--model/-m`, `--threshold`, `--output/-o`, `--port/-p`, `--headless`, `--cookies-file`, `--username`, `--password`, `--verbose/-v`, `--watch`, `--watch-interval`, `--auto-heal`, `--open-pr`, `--pr-base-branch`, `--pr-remote`, `--pr-dry-run`. Exit code 0.

### `python main.py api --help` ✅
Lists options: `--port/-p`, `--host`, `--verbose/-v`. Exit code 0.

### `python main.py batch --help` ✅
Lists options: `--config/-c` (required), `--verbose/-v`. Exit code 0.

### `python main.py export --help` ✅
Lists options: `--run-id/-r` (required), `--format/-f` (required, choices: markdown/docusaurus/nextra/mintlify/starlight), `--output-dir/-o`, `--verbose/-v`. Exit code 0.

**All 6 CLI help commands work correctly. All subcommands are accessible.**

---

## 6. API Server Test Results

**Startup:** Server starts successfully on port 8001 via `python main.py api --port 8001` (uses uvicorn with `reload=True` by default).

**Note on authentication:** The API uses `X-API-Key` header authentication controlled by env vars `READY_AI_API_KEY` / `READY_AI_API_KEYS`. When no keys are configured and `AUTH_DISABLED` is not set, all non-public endpoints return 401. Public endpoints (`/health`, `/ready`, `/docs`, `/openapi.json`, `/`) do not require auth. For full endpoint testing, the server was started with `AUTH_DISABLED=true` and `reload=False` (the WatchFiles reloader spawned a subprocess that did not inherit env vars, so `reload=False` was required for env-based config to take effect).

### Endpoint test results:

| Endpoint | Method | Status | Result |
|---|---|---|---|
| `/health` | GET | 200 | ✅ Returns `{"status":"healthy","service":"ready-ai","version":"0.1.0","timestamp":"..."}` |
| `/ready` | GET | 200 | ✅ Returns `{"status":"ready","checks":{"output_dir":"ok","browser_pool":"ok"},"timestamp":"..."}` |
| `/docs` | GET | 200 | ✅ Returns Swagger UI HTML |
| `/openapi.json` | GET | 200 | ✅ Returns valid OpenAPI 3.1.0 spec |
| `/runs` | GET | 200 | ✅ Returns `{"total":0,"runs":[]}` (empty initially) |
| `/runs` | POST | 200 | ✅ Creates a run, returns `run_id`, status `PLANNING` |
| `/runs/{run_id}` | GET | 200 | ✅ Returns run details (status became `FAILED` — see note below) |
| `/runs/{run_id}/output` | GET | 200 | ✅ Returns ZIP archive with `_metrics.json` and `_state.json` |
| `/runs/{run_id}/metrics` | GET | 404 | Expected — metrics not generated for failed run |
| `/runs/{run_id}/diff` | GET | 404 | Expected — `docs.md` not generated for failed run |
| `/doc-sets` | GET | 200 | ✅ Returns `{"total":0,"docs":[]}` |
| `/history` | GET | 200 | ✅ Returns `{"total":0,"records":[]}` |
| `/history/aggregates` | GET | 200 | ✅ Returns `{"total_runs":0}` |
| `/docs/{version}/status` | GET | 404 | Expected — no documentation for version `1.0.0` |

### OpenAPI registered endpoints:
```
/health        [GET]
/ready         [GET]
/runs          [POST, GET]
/runs/{run_id} [GET]
/runs/{run_id}/output    [GET]
/runs/{run_id}/metrics   [GET]
/runs/{run_id}/export    [POST]
/runs/{run_id}/diff      [GET]
/doc-sets      [GET]
/docs/{version}/status  [GET]
/history       [GET]
/history/aggregates  [GET]
```

### Run execution note:
A test run was created via `POST /runs` with `{"goal":"test","url":"https://example.com"}`. The run was accepted (status `PLANNING`, HTTP 200) but subsequently failed with:
```
litellm.AuthenticationError: AuthenticationError: OpenAIException - The api_key client option must be
set either by passing api_key to the client or by setting the OPENAI_API_KEY environment variable
```
This is **expected behavior** — no `OPENAI_API_KEY` was configured in the environment. The server handled the error gracefully and the run status correctly transitioned to `FAILED`. The error was logged but did not crash the server.

---

## 7. Chrome Availability

**Chrome IS available** for CDP (Chrome DevTools Protocol) connections.

- `where chrome` → not on PATH
- `C:\Program Files\Google\Chrome\Application\chrome.exe` → **EXISTS** ✅
- `C:\Program Files (x86)\Google\Chrome\Application\chrome.exe` → not present
- `%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe` → not present

Chrome is installed at the standard 64-bit Windows path and can be launched for CDP-based automation.

---

## 8. Runtime Errors & Issues Encountered

### Issue 1: Un-awaited AsyncMock coroutine warning (test code)
- **Location:** `src/agent/test_runner.py:206`
- **Warning:** `RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`
- **Affected tests:** `tests/test_e2e_doc_test.py::TestE2EDriftDetected::test_drift_detected`, `tests/test_e2e_doc_test.py::TestE2EReportStructure::test_report_json_structure`
- **Severity:** Low (cosmetic/test hygiene). Tests pass but an unnecessary coroutine object is created and discarded. Could mask real bugs in async test setup.
- **Suggested fix:** Ensure the mock at `test_runner.py:206` is configured as `AsyncMock` and properly awaited, or use `MagicMock` if synchronous behavior is intended.

### Issue 2: WatchFiles reloader does not inherit env vars (runtime/deployment)
- **Location:** `main.py:476` — `uvicorn.run("src.api.server:app", host=args.host, port=args.port, reload=True)`
- **Symptom:** When starting the API server with `reload=True`, the WatchFiles reloader spawns a child subprocess that does not inherit environment variables set in the parent shell (on Windows). This means `AUTH_DISABLED`, `READY_AI_API_KEY`, and other env-based configuration is lost in the worker process.
- **Severity:** Medium (affects development/local testing and any deployment that relies on env vars with the default `reload=True`). Production deployments typically set env vars at the OS/process level which the child does inherit, but shell-scoped env vars passed to `python main.py api` may not propagate.
- **Workaround:** Run uvicorn with `reload=False` for env-var-dependent configuration, or set env vars via `.env` file (loaded by `python-dotenv` in `main.py:28`).

### Issue 3: Zero coverage on `src/docs/text_diff.py`
- **Location:** `src/docs/text_diff.py` (126 statements)
- **Severity:** Medium (untested code is a quality risk). This module has 0% coverage — no tests exercise it at all.

### Issue 4: Low coverage on core execution paths
- **`src/agent/executor.py` (23%)** — The core agent executor is largely untested.
- **`src/agent/recovery.py` (21%)** — Recovery logic is largely untested.
- **`src/cdp/input.py` (20%)** — CDP input simulation is largely untested.
- **`src/llm/client.py` (35%)** — LLM client is partially tested.
- **Severity:** Medium. These are critical paths for the agent's functionality.

### Issue 5: No `.env` file present (only `.env.example`)
- The project ships `.env.example` but no `.env`. Running the agent or API server requires at minimum an `OPENAI_API_KEY` (or another LLM provider key). Without it, run creation succeeds but execution fails with `AuthenticationError`.
- **Severity:** Informational (expected for a project template).

---

## 9. Summary

| Check | Status |
|---|---|
| Python environment | ✅ 3.12.4, all dependencies installed |
| Project import (`import src`) | ✅ OK |
| Lint (`ruff check`) | ✅ All checks passed |
| Test suite (`pytest`) | ✅ 365 passed, 2 warnings |
| Coverage | 61% overall (4486 stmts, 1742 missed) |
| CLI help (all 6 commands) | ✅ All accessible and correct |
| API server startup | ✅ Starts on port 8001 |
| API `/health` | ✅ 200 |
| API `/ready` | ✅ 200 |
| API `/docs` (Swagger) | ✅ 200 |
| API `/openapi.json` | ✅ 200 |
| API `/runs` (GET/POST) | ✅ 200 |
| API `/runs/{id}` (GET) | ✅ 200 |
| API `/runs/{id}/output` | ✅ 200 (ZIP) |
| API `/doc-sets` | ✅ 200 |
| API `/history` | ✅ 200 |
| API `/history/aggregates` | ✅ 200 |
| Chrome availability | ✅ Available at `C:\Program Files\Google\Chrome\Application\chrome.exe` |

**Overall:** The project is in a healthy state. All 365 tests pass, lint is clean, the CLI works, and the API server is fully functional. The main areas for improvement are test coverage on core execution paths (`executor.py`, `recovery.py`, `input.py`, `text_diff.py`) and the minor AsyncMock warning in test code.
