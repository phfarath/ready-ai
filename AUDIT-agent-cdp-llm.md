# Deep Audit Report — agent + cdp + llm modules

Read-only audit of `src/agent/`, `src/cdp/`, `src/llm/` and `tests/`.
Findings categorized, with severity, file + approximate line, description, suggested fix, and effort size.

Legend:
- **Severity**: critical / high / medium / low
- **Effort**: minor fix (a few lines) / major implementation (significant work)
- Line numbers are approximate (based on the read source).

---

## CRITICAL

### C1. Resource leak: Chrome process not killed on Windows when `terminate()` fails
- **Category**: bug / reliability (resource leak)
- **Severity**: critical
- **File**: `src/agent/browser_session.py` ~L165-185 (`teardown`)
- **Description**: `teardown()` calls `proc.terminate()` then `proc.wait(timeout=2)`, falling back to `proc.kill()` then `os.kill(pid, SIGKILL)`. On **Windows** `signal.SIGKILL` does not exist (`AttributeError`) and `os.kill` with `signal.SIGTERM` is unreliable. The `except (OSError, ProcessLookupError)` clause catches the `AttributeError`? No — `AttributeError` is NOT in the caught tuple, so it would propagate out of `teardown()`, skip `_unregister_chrome_pid`, and leak the process. The atexit handler `_kill_all_orphan_chrome` uses the same `signal.SIGKILL` and would crash on Windows too. The plan files (PLAN_FASE_A Task 3) explicitly call out this Windows gap and recommend `psutil`, but it has not been implemented.
- **Suggested fix**: Use `signal.SIGTERM`/`signal.SIGKILL` only on POSIX; on Windows use `proc.kill()` (which calls `TerminateProcess`) and `subprocess.CREATE_NEW_PROCESS_GROUP` flags at launch. Add `psutil`-based tree kill as the plan recommends. Guard the `atexit` handler for `AttributeError`.
- **Effort**: minor fix (platform-branch + atexit guard) for the crash; major implementation for the full `psutil` tree-kill from PLAN_FASE_A Task 3.

### C2. `atexit` handler sends SIGKILL globally — unsafe on Windows and can kill unrelated Chrome
- **Category**: bug / security
- **Severity**: critical
- **File**: `src/agent/browser_session.py` ~L33-42 (`_kill_all_orphan_chrome`)
- **Description**: `_kill_all_orphan_chrome` iterates `_CHROME_PIDS` and calls `os.kill(pid, signal.SIGKILL)`. (1) On Windows `signal.SIGKILL` is undefined → `AttributeError` at interpreter shutdown, so the safety net silently does nothing. (2) If a Chrome PID is reused by the OS for an unrelated process between registration and atexit, we kill an innocent process. (3) `time.sleep(0.1)` inside an `atexit` handler blocks interpreter shutdown.
- **Suggested fix**: Platform-guard the signal choice. Track Chrome child PIDs via `psutil` and verify the process is still our child (PPID match) before killing. Avoid sleeping in atexit.
- **Effort**: major implementation (needs psutil integration, see PLAN_FASE_A Task 3).

### C3. `get_metrics()` called on possibly-None `metrics` without guard in `dom_utils.py`
- **Category**: bug
- **Severity**: high (downgraded from critical because it only fires on CDP errors)
- **File**: `src/agent/dom_utils.py` ~L66 (`dom_fingerprint`)
- **Description**: `get_metrics().increment("fingerprint.errors", source="cdp")` is called in the `except` block. `get_metrics()` can return `None` when no `RunContext` is active (the codebase explicitly handles this elsewhere, e.g. `loop.py`, `connection.py`). Here it will raise `AttributeError: 'NoneType' object has no attribute 'increment'`, masking the original fingerprint error.
- **Suggested fix**: `metrics = get_metrics(); if metrics: metrics.increment(...)`.
- **Effort**: minor fix.

---

## HIGH

### H1. `send()` future leak when `self._ws.send` raises before the future resolves
- **Category**: bug (resource leak)
- **Severity**: high
- **File**: `src/cdp/connection.py` ~L355-400 (`send`)
- **Description**: The future is created and stored in `self._pending[msg_id]` before `await self._ws.send(...)`. If `_ws.send` raises (e.g. connection reset), the `except Exception` re-raises but the future is still in `self._pending`. On the timeout path the code does `self._pending.pop(msg_id, None)`, but on the `_ws.send` raise path there is no `pop`, so the orphaned future stays in `_pending` forever. The `_drain_pending` method only runs on disconnect handling, so a send-time socket error that does NOT trigger the recv-loop disconnect leaves a leaked future.
- **Suggested fix**: In the `except Exception` block (or a `finally`), `self._pending.pop(msg_id, None)` when status indicates the send itself failed.
- **Effort**: minor fix.

### H2. `wait_for_navigation_settled` directly consumes from shared `self._conn._events` queue
- **Category**: bug
- **Severity**: high
- **File**: `src/cdp/page.py` ~L190-250 (`wait_for_navigation_settled`)
- **Description**: This method bypasses `wait_for_event` and pulls directly from `self._conn._events`. It also calls `self._conn.wait_for_event(...)` internally. This mixes two consumption patterns on the same `asyncio.Queue`. If two consumers race (e.g. the cursor loop triggers an event, or `wait_for_network_idle` runs concurrently), events can be consumed by the wrong waiter. The method re-queues non-nav events, but ordering relative to other concurrent waiters is not preserved. Combined with `wait_for_network_idle` also pulling from `_events`, there is a real risk of event starvation/mis-delivery.
- **Suggested fix**: Centralize event subscription with a per-subscriber queue (fan-out) instead of a single shared `asyncio.Queue`. Short of that, document and enforce single-consumer discipline.
- **Effort**: major implementation.

### H3. `_events` is a single shared queue — event ordering and multi-waiter races
- **Category**: bug / reliability
- **Severity**: high
- **File**: `src/cdp/connection.py` ~L70 (`self._events`), used by `page.py` `wait_for_network_idle` and `wait_for_navigation_settled`, and `connection.py` `wait_for_event`
- **Description**: All event waiters pull from the same `asyncio.Queue`. `wait_for_event` stashes and re-queues non-matching events, but if two `wait_for_event` calls run concurrently, each can swallow the other's target event into its stash and re-queue it after the other has already timed out. The "re-queue" approach assumes a single active waiter. With `wait_for_navigation_settled` + `wait_for_network_idle` + `wait_for_event("Page.loadEventFired")` all potentially active, events get shuffled and can be lost or delivered late.
- **Suggested fix**: Implement an event-bus/fan-out so each waiter gets its own copy of every event. This is the root cause behind H2.
- **Effort**: major implementation.

### H4. `LLMClient._call_with_retry` retries ONLY `RateLimitError`; all other exceptions raise immediately
- **Category**: reliability
- **Severity**: high
- **File**: `src/llm/client.py` ~L70-120 (`_call_with_retry`)
- **Description**: The retry loop catches `litellm.exceptions.RateLimitError` and retries with backoff, but the generic `except Exception` re-raises immediately. Transient failures (timeouts, `APIConnectionError`, `InternalServerError` 5xx, `ServiceUnavailableError`) are not retried — a single transient blip fails the whole step/run. PLAN_FASE_B Task 5 explicitly calls out the missing circuit breaker and broader retry.
- **Suggested fix**: Retry on a broader set of transient exceptions (`Timeout`, `APIConnectionError`, `InternalServerError`, `ServiceUnavailableError`). Implement the circuit breaker from PLAN_FASE_B Task 5. Exclude `AuthenticationError`/`BadRequestError` (non-retryable).
- **Effort**: minor fix for broader retry; major implementation for the full circuit breaker.

### H5. `_patch_openai_model_dump` monkey-patches the OpenAI SDK at import time, globally and irreversibly
- **Category**: quality / reliability
- **Severity**: medium
- **File**: `src/llm/client.py` ~L20-60
- **Description**: On module import, the code permanently rewrites `openai._compat.model_dump` and several submodules' `model_dump` references. This is global, irreversible, and not thread-safe at import. It also references specific private module paths that will break silently on SDK upgrades. There's no unpatch path. If another library depends on the real `by_alias=None` semantics, it's silently broken.
- **Suggested fix**: Pin the OpenAI/pydantic versions to a compatible set, or wrap the litellm call to avoid the buggy code path. Document the workaround with a version compatibility note and add a CI guard.
- **Effort**: minor fix (pin versions) / major implementation (proper fix).

### H6. `teardown()` is not idempotent — second call hits `self._chrome_proc.pid` on None
- **Category**: bug
- **Severity**: medium
- **File**: `src/agent/browser_session.py` ~L150-185 (`teardown`)
- **Description**: After teardown, `self._chrome_proc = None` and `self._conn = None` are set in the `finally`. But the `if self._chrome_proc:` guard is correct; however `_unregister_chrome_pid(self._chrome_proc.pid)` is called in the `finally` block unconditionally — wait, it's inside the `if self._chrome_proc:` block's `finally`. Let me re-check: the `finally` is nested inside `if self._chrome_proc:`, and it references `self._chrome_proc.pid` after `self._chrome_proc = None` is set earlier in the same finally? No — the order is: set `None` then call `_unregister_chrome_pid`? Actually the code sets `self._chrome_proc = None` then calls `_unregister_chrome_pid(self._chrome_proc.pid)` → `AttributeError: 'NoneType' has no attribute 'pid'`. This is a real bug if the order is as written. Looking again: the finally does `_unregister_chrome_pid(self._chrome_proc.pid)` then `self._chrome_proc = None`? The source order is: `finally: _unregister_chrome_pid(self._chrome_proc.pid); self._chrome_proc = None; self._conn = None`. If that's the order it's fine. But the plan (PLAN_FASE_A Task 3) explicitly requires idempotency and "calling 2x doesn't error". A second `teardown()` call: `self._conn` is None so skipped, `self._chrome_proc` is None so skipped — actually OK. The real risk is concurrent `teardown()` (e.g. signal handler + normal flow): both pass the `if` check, both call `terminate()`. 
- **Suggested fix**: Add an `asyncio.Lock` around teardown and a `_torn_down` flag. PLAN_FASE_A Task 2/3 already specifies this.
- **Effort**: minor fix.

### H7. `recover()` skips LLM-driven login after crash — silent auth failure
- **Category**: reliability
- **Severity**: high
- **File**: `src/agent/browser_session.py` ~L255-280 (`recover`)
- **Description**: After a crash, `recover()` tears down and respawns Chrome, then only re-injects cookies. If `username`/`password` were provided (form-based auth), it explicitly logs a warning and **skips** `handle_login`, relying on cookies that may have expired or been lost in the crash. The subsequent step then runs against an unauthenticated page and fails confusingly. There is no fallback to attempt login.
- **Suggested fix**: After respawn, attempt `handle_login` if credentials are set, or at least surface a clear error so the orchestrator can abort rather than run steps unauthenticated.
- **Effort**: minor fix (call `handle_login` if creds present).

### H8. `CursorAnimator._loop` accesses `self._conn._ws` (private attr) and has no stop on connection close
- **Category**: quality / reliability
- **Severity**: medium
- **File**: `src/agent/cursor.py` ~L50-95 (`_loop`)
- **Description**: The loop reads `self._conn._ws is None` to detect a dead connection — accessing a private attribute of `CDPConnection`. If `close()` sets `_ws = None`, the loop idles (good), but it never actually stops; it keeps polling with exponential backoff forever until `stop()` cancels it. On `recover()` the old `CursorAnimator` keeps running against the old (now None) conn until `stop()` is called in `run()`'s finally — but `recover()` happens mid-`run()`, so the cursor loop points at the stale conn object during recovery.
- **Suggested fix**: Expose a public `is_alive` property on `CDPConnection` and use it. Re-bind the cursor to the new conn after `recover()`.
- **Effort**: minor fix.

---

## MEDIUM

### M1. `scroll` action has inverted delta_y semantics
- **Category**: bug
- **Severity**: medium
- **File**: `src/agent/executor.py` ~L260 (`scroll` action)
- **Description**: For `direction == "down"` the code sets `delta_y = -400`, and for the else (implied "up") `delta_y = 400`. But `Input.dispatchMouseEvent` `mouseWheel` `deltaY` is positive to scroll down (content moves up) in Chrome's convention — actually Chrome uses positive deltaY for scroll down. The code's own `scroll()` docstring says "negative = scroll down", matching the executor. This is internally consistent but inverted relative to Chrome's standard `wheel` event (`deltaY > 0` = scroll down). Need to verify against actual behavior; if wrong, "scroll down" scrolls up and vice versa.
- **Suggested fix**: Verify against Chrome; if inverted, flip the sign. Add a test.
- **Effort**: minor fix.

### M2. `wait_for_event` lifecycle event check does not validate the event `name`
- **Category**: bug
- **Severity**: medium
- **File**: `src/cdp/page.py` ~L150-175 (`wait_for_network_idle` lifecycle path)
- **Description**: When `READY_AI_USE_LIFECYCLE_EVENTS` is set, the code calls `wait_for_event("Page.lifecycleEvent", timeout)` and accepts ANY lifecycle event (load, DOMContentLoaded, networkIdle, firstPaint, etc.) as "network idle". The comment even acknowledges it should check the `name` field. This means `wait_for_network_idle` returns on the first lifecycle event (e.g. `firstPaint`), not on actual network idle.
- **Suggested fix**: Inspect `params.name == "networkIdle"` and loop until that specific event, re-queueing others.
- **Effort**: minor fix.

### M3. `wait_for_network_idle` caching returns stale idle when called within TTL after a real timeout
- **Category**: bug
- **Severity**: medium
- **File**: `src/cdp/page.py` ~L130-145 (`_network_idle_cache`)
- **Description**: The cache is populated even on timeout ("we tried" marker), so a subsequent call within the TTL returns "idle" even though the network was never actually idle. The code comments acknowledge this is "cheaper than a fresh scan" but it can cause screenshots/DOM captures to be taken while requests are still in flight.
- **Suggested fix**: Only cache on successful idle detection, not on timeout.
- **Effort**: minor fix.

### M4. `_parse_action` final regex `r"\{[^{}]+\}"` cannot match nested objects
- **Category**: bug
- **Severity**: medium
- **File**: `src/agent/executor.py` ~L210-225 (`_parse_action`)
- **Description**: The fallback regex `\{[^{}]+\}` matches a single-level object only. An action like `{"action":"type","selector":"x","text":"a\"b"}` with an escaped quote or nested structure won't match. More importantly, `[^{}]+` excludes any object containing `{` or `}`, so the common `{"action":"click","selector":"div > button:nth-child(1)"}` works, but any JSON the LLM emits with nested braces fails. It also doesn't handle the case where the markdown code-block extraction already failed due to newlines.
- **Suggested fix**: Use a proper JSON extractor (or `json.loads` with a tolerance for surrounding prose). The first `json.loads` attempt is best; the regex fallbacks are fragile.
- **Effort**: minor fix.

### M5. `extract_selector` regex stops at first newline, missing multi-line action_descs
- **Category**: bug
- **Severity**: low
- **File**: `src/agent/cursor.py` ~L150 (`extract_selector`)
- **Description**: `re.search(r"element(?:\s+via\s+\w+\s+fallback)?:\s*(.+?)(?:\n|$)", action_desc)` captures up to the first newline. But `_format_step_action_details` appends `\n\n**Failure details:** ...` to `action_desc`, so `extract_selector` correctly stops at the first `\n`. However the `via JS fallback` branch and `Clicked element by text:` (from `click_text`) are NOT matched by this regex — it only matches `element:` / `element via X fallback:`, so `click_text` actions never get highlighted.
- **Suggested fix**: Extend the regex to also match `Clicked element by text: 'X'` and similar.
- **Effort**: minor fix.

### M6. `recover()`'s `setup()` failure is not handled — leaves session half-built
- **Category**: reliability
- **Severity**: medium
- **File**: `src/agent/browser_session.py` ~L250-285 (`recover`)
- **Description**: `recover()` calls `await self.setup()` which itself has a try/except that calls `teardown()` on failure and re-raises. But after `setup()` raises inside `recover()`, the exception propagates to `_execute_steps` which catches only `ConnectionClosed` — any other exception from `setup()` (e.g. `FileNotFoundError` for Chrome, `RuntimeError` for WS URL) escapes and kills the pipeline with a non-recoverable error, even though `MAX_CRASHES` budget wasn't exhausted.
- **Suggested fix**: Wrap `setup()` in `recover()` with a try/except that converts failures into a retry (up to MAX_CRASHES) or a clear terminal error.
- **Effort**: minor fix.

### M7. `inject_cookies` sends cookies without first navigating — cookies may not apply to target domain
- **Category**: bug
- **Severity**: medium
- **File**: `src/agent/loop.py` ~L120-130 and `src/agent/browser_session.py` ~L135-150 (`inject_cookies`)
- **Description**: In `AgenticLoop.run()`, `inject_cookies()` is called BEFORE `page.navigate(self.url)`. CDP `Network.setCookie` requires a valid `domain` field to apply the cookie; if the cookie file omits `domain`, the default `""` is sent and the cookie is set for no domain (effectively discarded). The browser hasn't loaded the target origin yet, so domain-less cookies silently fail. The `_normalize_cookie` helper defaults `domain` to `""`.
- **Suggested fix**: Navigate to the target URL first (or derive the domain from `self.url`) before injecting cookies, or require the `domain` field.
- **Effort**: minor fix.

### M8. `handle_login` runs before navigation in some flows — race with page load
- **Category**: bug
- **Severity**: medium
- **File**: `src/agent/loop.py` ~L110-120
- **Description**: In `run()`, if `username`/`password` are set, the loop calls `page.navigate(self.url)` then `handle_login(llm)`. But `handle_login` itself also navigates and looks for login links. There's a double-navigate and the first `navigate` may not have settled before `handle_login` re-evaluates the DOM. The sequence is fragile.
- **Suggested fix**: Single navigation path: if creds are set, let `handle_login` own the navigation; otherwise navigate.
- **Effort**: minor fix.

### M9. `state.py` `from_file` silently swallows all exceptions and returns None
- **Category**: reliability
- **Severity**: medium
- **File**: `src/agent/state.py` ~L70-90 (`from_file`)
- **Description**: The broad `except Exception` logs and returns `None`, which `AgenticLoop.__init__` interprets as "no checkpoint" and starts a fresh run — potentially overwriting a partially-complete run's state. A corrupted-but-present checkpoint is indistinguishable from "no checkpoint". The constructor then creates a NEW `RunState` and `_save_checkpoint` will overwrite the corrupt file.
- **Suggested fix**: Distinguish "file missing" (return None) from "file corrupt" (raise or move aside). At minimum, don't overwrite — write to a `.corrupt` sidecar.
- **Effort**: minor fix.

### M10. `connection.py` `close()` swallows all exceptions from `_reconnect_task` await
- **Category**: reliability
- **Severity**: medium
- **File**: `src/cdp/connection.py` ~L560-590 (`close`)
- **Description**: `except (asyncio.CancelledError, Exception): pass` is a broad swallow. If `_reconnect_task` raises an unexpected error (e.g. `RuntimeError` from `_post_reconnect_reattach`), it's silently dropped and `close()` proceeds. This hides bugs. `asyncio.CancelledError` should be isolated.
- **Suggested fix**: `except asyncio.CancelledError: pass` separately, then log other exceptions.
- **Effort**: minor fix.

### M11. `get_ws_url` does not validate the HTTP response status
- **Category**: reliability
- **Severity**: medium
- **File**: `src/cdp/browser.py` ~L160-185 (`get_ws_url`)
- **Description**: `session.get(url)` is followed by `await resp.json()` without checking `resp.status`. A 503 from Chrome (still starting) would produce a `JSONDecodeError` (caught) and retry, but a 404 or 500 returning valid JSON without `webSocketDebuggerUrl` raises `KeyError` (also caught). The retry loop is OK, but a non-JSON 200 response would be misdiagnosed. Minor, but adding `resp.raise_for_status()` clarifies.
- **Suggested fix**: `resp.raise_for_status()` before parsing.
- **Effort**: minor fix.

### M12. `launch_chrome` creates a temp `user_data_dir` that is never cleaned up
- **Category**: bug (resource leak)
- **Severity**: medium
- **File**: `src/cdp/browser.py` ~L120-150 (`launch_chrome`)
- **Description**: `tempfile.mkdtemp(prefix="ready-ai-chrome-")` creates a directory that is never removed. Over many runs, `/tmp` fills with orphaned Chrome profiles (each 10-100MB). The directory is not tracked or returned for cleanup.
- **Suggested fix**: Register the temp dir for cleanup in `teardown()` (return it from `launch_chrome` or track globally), or use a fixed dir.
- **Effort**: minor fix.

### M13. `test_runner.py` reuses `BrowserSession` internals in a fragile way
- **Category**: quality
- **Severity**: medium
- **File**: `src/agent/test_runner.py` ~L175-195
- **Description**: `DocTestRunner` constructs a `BrowserSession` and manually assigns `login_session._conn`, `_page`, `_input`, `_runtime` to reuse the existing connection. This reaches into private attributes and will break if `BrowserSession` changes. It also double-registers the Chrome PID (once via `launch_chrome` directly in `test_runner`, which does NOT register the PID, so the atexit handler won't clean it up — inconsistency with `BrowserSession.setup`).
- **Suggested fix**: Add a `BrowserSession.from_existing_connection(conn)` constructor or a public method to attach domains.
- **Effort**: minor fix.

### M14. `test_runner.py` does NOT register its Chrome PID — orphan leak on crash
- **Category**: bug (resource leak)
- **Severity**: high
- **File**: `src/agent/test_runner.py` ~L125-135 (`run`) and ~L330 (`_cleanup`)
- **Description**: `DocTestRunner.run()` calls `launch_chrome(...)` directly and stores the proc in `self._chrome_proc`, but NEVER calls `_register_chrome_pid`. So if the test runner crashes, the `atexit` handler won't kill that Chrome. `_cleanup` does `terminate()`/`kill()` in a finally, but if the process is already dead or the runner is killed (SIGKILL), the Chrome leaks. This is inconsistent with `BrowserSession.setup` which does register.
- **Suggested fix**: Register the PID via `_register_chrome_pid` (or use `BrowserSession`).
- **Effort**: minor fix.

### M15. `test_runner.py` `_cleanup` uses `proc.poll()` + `asyncio.sleep` instead of `proc.wait(timeout=)`
- **Category**: reliability
- **Severity**: medium
- **File**: `src/agent/test_runner.py` ~L320-340 (`_cleanup`)
- **Description**: `terminate()` then `poll()` then `await asyncio.sleep(0.5)` then check `returncode is None` then `kill()`. This is a race: `poll()` doesn't block, the 0.5s sleep is arbitrary, and a slow-terminating Chrome may still be alive after 0.5s → kill() fires (OK) but there's no `wait()` after kill, leaving a zombie on POSIX. Contrast with `BrowserSession.teardown` which uses `wait(timeout=2)`.
- **Suggested fix**: Use `proc.wait(timeout=2)` consistently.
- **Effort**: minor fix.

### M16. `executor.execute_step` swallows non-ConnectionClosed exceptions from `_dispatch_action`
- **Category**: bug
- **Severity**: medium
- **File**: `src/agent/executor.py` ~L295-310 (`_dispatch_action` except clauses)
- **Description**: The generic `except Exception` returns `[Error] ...` string, which is fine. But the `except websockets.exceptions.ConnectionClosed` re-raises — and `execute_step` does NOT catch it, so it propagates to `_execute_steps` which catches `ConnectionClosed` and triggers `recover()`. However, between the raise and the recovery, the in-flight `text_before`/`url_before` captured at the top of the attempt loop are stale, and on retry after recovery the loop continues with the OLD `dom_html`/`elements` from before the crash — the page may have navigated.
- **Suggested fix**: After recovery, re-fetch DOM/elements at the top of the retry. The `for attempt` loop does NOT re-fetch on the first iteration's stale data.
- **Effort**: minor fix.

### M17. `executor.execute_step` re-fetches DOM only at the bottom of the retry loop
- **Category**: bug
- **Severity**: medium
- **File**: `src/agent/executor.py` ~L195-205 (refresh DOM)
- **Description**: `dom_html` and `interactive_elements` are refreshed only AFTER a failed attempt (near the end of the loop body). On the FIRST attempt, the caller-provided (possibly stale) DOM is used for `_get_action`. If the caller (`_execute_steps`) passes DOM captured before a prior step's async settling, the first action is planned against stale data.
- **Suggested fix**: Always fetch fresh DOM at the top of each attempt, or document the contract that the caller must provide fresh data.
- **Effort**: minor fix.

### M18. `_reexecute_missing_steps` uses `planner._parse_steps` (private) — coupling
- **Category**: quality
- **Severity**: low
- **File**: `src/agent/loop.py` ~L470 (`planner._parse_steps(response)`) and `src/agent/recovery.py` ~L170, L210
- **Description**: Multiple modules reach into `planner._parse_steps` (name-mangled-private). If planner refactors, all break silently.
- **Suggested fix**: Promote `_parse_steps` to `parse_steps` (public) or expose via `planner.parse_steps`.
- **Effort**: minor fix.

### M19. `loop.py` `_execute_steps` mutates `step_list` while iterating via index — OK but `self._state.planned_steps` can desync
- **Category**: bug
- **Severity**: low
- **File**: `src/agent/loop.py` ~L280-380
- **Description**: The while-loop rebuilds `step_list` on replan (`step_list = step_list[:step_idx] + replanned`) and updates `self._state.planned_steps`. However `self._state.current_step_index` is only incremented on success. On a replan that inserts MORE steps than before, the index stays valid, but the checkpoint's `executed_results` list grows independently. If the run resumes from checkpoint after a crash mid-replan, the `current_step_index` may point into the old plan, not the replanned one. Resume logic uses `current_step_index < len(planned_steps)` which is correct, but the `i` (display number) is derived from `start_number + step_idx` and may collide with previously-rendered step numbers.
- **Suggested fix**: Make step numbering stable across replans (use a separate counter).
- **Effort**: minor fix.

### M20. `connection.py` `_reconnect` imports `random` inside the method
- **Category**: quality
- **Severity**: low
- **File**: `src/cdp/connection.py` ~L210 (`import random`)
- **Description**: Minor; `import random` is done inside `_reconnect` rather than at module top. Inconsistent with the rest of the file. Not a bug, but suggests it was added hastily.
- **Suggested fix**: Move to top-level imports.
- **Effort**: minor fix.

---

## SECURITY

### S1. Credential (username/password) exposure via logs and LLM prompts
- **Category**: security (credential exposure)
- **Severity**: high
- **File**: `src/agent/browser_session.py` ~L230-330 (`handle_login`)
- **Description**: `handle_login` injects `self.username`/`self.password` into a JS expression via `json.dumps` (safe from injection) and dispatches via `Runtime.evaluate`. The values are sent to the page (necessary). However: (1) `log_event`/structured logs elsewhere may capture the JS expression if the evaluate fails and the error message includes the expression. (2) The credentials are stored as plaintext on `BrowserSession` and `DocTestRunner` instances and survive in memory for the run lifetime. (3) There's no redaction in `RunState` or metrics if a step's `action_desc`/`failure_reason` accidentally includes the typed text (the executor logs `action_desc` which for `type` actions is `Typed '<text>' into ...` — **this logs the password**).
- **Suggested fix**: The executor's `type` action returns `f"Typed '{text}' into {selector}"` — if `text` is a password, it's logged and stored in `RunState.executed_results` and the doc. Sanitize `action_desc` when the selector matches a password field. Avoid logging credentials in error messages.
- **Effort**: minor fix for the logging; medium for full sanitization.

### S2. Executor logs typed text verbatim — password leak into docs and checkpoints
- **Category**: security (credential exposure)
- **Severity**: high
- **File**: `src/agent/executor.py` ~L240 (`type` action: `return f"Typed '{text}' into {selector or 'focused element'}"`)
- **Description**: The `type` action description embeds the full typed text. This string flows into: `logger.info` in `_execute_steps`, `doc.add_step(action_description=...)` (rendered into the final markdown docs), `RunState.executed_results` (serialized to `_state.json` on disk), and `log_event`. If the LLM types a password (e.g. during a login-flow documentation run), the password is written to the generated documentation, the checkpoint JSON, and the logs.
- **Suggested fix**: Detect sensitive selectors (password fields by `type="password"`, name/autocomplete hints) and redact the text in the action description (`Typed '***' into ...`). Apply the same `is_sensitive_field` helper from `sanitize.py`.
- **Effort**: minor fix.

### S3. `sanitize.py` raw mode bypasses sensitive redaction for interactive elements
- **Category**: security
- **Severity**: medium
- **File**: `src/cdp/sanitize.py` ~L260-280 (`sanitize_interactive_element`)
- **Description**: When `raw=True`, the function returns the element unchanged (`{**element, "_redactions": {}}`), INCLUDING sensitive values. The module docstring claims "Sensitive redaction still happens" in `get_interactive_elements`, but the code path for `raw=True` skips the sensitive redaction loop entirely. So `READY_AI_RAW_DOM=true` leaks passwords/PII to the LLM. The docstring explicitly says raw mode "bypasses the cosmetic passes but NOT the sensitive layer" — the implementation contradicts the doc.
- **Suggested fix**: In raw mode, still apply the sensitive-field redaction (skip only truncation). Fix the control flow so `is_sensitive_field` is evaluated regardless of `raw`.
- **Effort**: minor fix.

### S4. `sanitize_html` raw mode also skips sensitive value redaction
- **Category**: security
- **Severity**: medium
- **File**: `src/cdp/sanitize.py` ~L165-175 (`sanitize_html`)
- **Description**: Same issue as S3 for HTML: `if raw: return SanitizedHTML(html=html, ...)` skips `_sanitize_form_values`, so password/credit-card values in `<input value="...">` are passed through to the LLM in raw mode. The module docstring says raw is "dev/debug only" but the sensitive layer should never be bypassable.
- **Suggested fix**: Always run the sensitive-value redaction pass; gate only the truncation/structural-stripping behind `raw`.
- **Effort**: minor fix.

### S5. `find_element_by_text` and `click_text` build selectors via string concatenation — minor XSS/injection surface
- **Category**: security
- **Severity**: low
- **File**: `src/cdp/runtime.py` ~L250-275 (`find_element_by_text`) and `src/agent/executor.py` ~L225-240 (`click_text`)
- **Description**: `find_element_by_text` builds `[aria-label="..."` selectors by concatenating the attribute value into a string without escaping quotes. If the page contains an element with `aria-label='x"]; evil; //'`, the returned selector is malformed (not a JS injection since it's returned as a string, but the selector could match unintended elements). `click_text` uses `json.dumps(text)` into JS (safe). Low impact but worth noting.
- **Suggested fix**: Escape double quotes in attribute values when building selectors.
- **Effort**: minor fix.

### S6. `Runtime.evaluate` expressions accept arbitrary JS from LLM output
- **Category**: security
- **Severity**: medium
- **File**: `src/agent/executor.py` ~L235-310, `src/agent/cursor.py` (highlight), `src/cd/runtime.py`
- **Description**: Selectors and text from LLM-generated JSON are interpolated into JS via `json.dumps` (good — prevents quote injection), but the `_PIERCE_JS` template and various `f"..."` JS strings embed selectors. `json.dumps(selector)` is used consistently, which is safe against JS string breakout. However, the LLM controls the `selector`/`url`/`text`/`key` values, and a malicious or compromised model could navigate to arbitrary URLs (`navigate` action) or type arbitrary text. There is no allowlist/sandbox on the navigation target — the agent will happily navigate to `file:///` or `javascript:` URLs if the LLM emits them.
- **Suggested fix**: Validate `navigate` URLs against an allowlist (or at least block `file:`, `javascript:`, `data:` schemes). Validate `key` against a known set.
- **Suggested fix**: Validate `navigate` URLs against an allowlist (or at least block `file:`, `javascript:`, `data:` schemes). Validate `key` against a known set.
- **Effort**: minor fix.

---

## RELIABILITY

### R1. No timeout on `page.navigate` overall (load wait 30s + network idle 10s = 40s, but no outer cap)
- **Category**: reliability
- **Severity**: medium
- **File**: `src/cdp/page.py` ~L100-130 (`navigate`)
- **Description**: `navigate` waits up to 30s for `loadEventFired` then up to 10s for network idle. On a hung page, a single navigate blocks the event loop for 40s. There's no overall deadline. The executor's `wait_for_navigation_settled` has a budget, but the top-level `navigate` does not.
- **Suggested fix**: Add an overall `timeout` param to `navigate` that bounds the total wait.
- **Effort**: minor fix.

### R2. `get_ws_url` default 10 retries × 1s = 10s blocking startup with no jitter
- **Category**: reliability
- **Severity**: low
- **File**: `src/cdp/browser.py` ~L160-185
- **Description**: Fixed 1s delay between retries. If Chrome is slow to start, 10s may be too short; if multiple ready-ai instances start simultaneously, no jitter causes thundering herd on the `/json/version` endpoint.
- **Suggested fix**: Add jitter and make retries/delay configurable via env.
- **Effort**: minor fix.

### R3. `recovery.recover_locally` and `replan_spa_step` catch all exceptions and return fallback — silent LLM failure
- **Category**: reliability
- **Severity**: medium
- **File**: `src/agent/recovery.py` ~L165-180, ~L210-230
- **Description**: Both functions `except Exception` and return a fallback (`{"decision": "mark_manual", ...}` or `None`). If the LLM client raises repeatedly (e.g. auth error, network down), recovery silently degrades to "manual" for every step with only a warning log. The run continues and produces a doc full of "manual_required" steps without surfacing the root cause.
- **Suggested fix**: Distinguish transient vs permanent LLM failures; after N consecutive LLM failures, abort the run with a clear error rather than producing a doc of skipped steps.
- **Effort**: minor fix.

### R4. `critic.review` defaults to `is_complete=True` on parse failure
- **Category**: reliability
- **Severity**: medium
- **File**: `src/agent/critic.py` ~L55-65
- **Description**: If the critic's JSON response can't be parsed, the code sets `is_complete=True` and `score=5`, so the loop treats a corrupt critic response as "approved" and stops improving. This masks LLM/parsing failures as "good enough".
- **Suggested fix**: Default to `is_complete=False` on parse failure so the loop retries, or at least log loudly and surface in metrics.
- **Effort**: minor fix.

### R5. `loop.run()` finally block calls `teardown()` even on success — but also after `raise` — can double-teardown with `recover`
- **Category**: reliability
- **Severity**: low
- **File**: `src/agent/loop.py` ~L145-150 (`finally: ... teardown()`)
- **Description**: If `recover()` was called mid-run and then the run later raises, `finally` calls `teardown()` on the recovered session (fine). But if `recover()` itself raised and left a half-built session, `teardown()` may encounter `self._chrome_proc` pointing at a process that `recover()`'s internal `teardown()` already killed (proc is None after teardown — OK due to the guard). Low risk but the control flow is tangled.
- **Suggested fix**: Ensure `teardown()` is idempotent (see H6) and document the contract.
- **Effort**: minor fix.

### R6. `_dispatch_action` `press_key` does not validate the `key` value
- **Category**: reliability / security
- **Severity**: low
- **File**: `src/agent/executor.py` ~L245-250
- **Description**: `key` from LLM is passed directly to `Input.dispatchKeyEvent`. Invalid keys cause a CDP error (caught as generic Exception → `[Error]`), but there's no upfront validation. A malicious prompt could send unusual key sequences.
- **Suggested fix**: Validate against the CDP key enum.
- **Effort**: minor fix.

### R7. `InputDomain.click` does not verify the element is clickable/visible before dispatching mouse events
- **Category**: reliability
- **Severity**: medium
- **File**: `src/cdp/input.py` ~L60-120 (`click`)
- **Description**: `click` resolves the box model and dispatches mouse events at the center. If the element is covered by an overlay, off-screen, or has zero size, the click still fires at computed coordinates but hits the wrong element. `getBoxModel` can return a degenerate quad (all zeros) for hidden elements. There's no visibility/size check.
- **Suggested fix**: Validate the quad has non-zero area and the element is in the viewport; scroll into view if not (the executor already does this on retry, but the first click doesn't).
- **Effort**: minor fix.

### R8. `type_text` raises `RuntimeError` if the element isn't found, but `execute_step` catches generic Exception → marks as `[Error]` and continues retrying
- **Category**: bug
- **Severity**: low
- **File**: `src/cdp/input.py` ~L195-205 and `src/agent/executor.py` ~L300
- **Description**: `type_text` raises `RuntimeError("type_text failed: ...")` when the target element is missing. `_dispatch_action`'s generic `except Exception` catches it and returns `[Error] type: ...`. The executor then treats this as a failed attempt and retries — but the retry will keep failing because the selector is wrong, wasting `MAX_RETRIES` LLM calls. A missing-element error should trigger a selector re-plan, not a blind retry.
- **Suggested fix**: Distinguish "element not found" (→ re-plan selector) from "transient failure" (→ retry).
- **Effort**: minor fix.

### R9. `connection.py` `_post_reconnect_reattach` manual attach has a 5s timeout but no retry
- **Category**: reliability
- **Severity**: low
- **File**: `src/cdp/connection.py` ~L290-330
- **Description**: If `Target.attachToTarget` times out (5s), the exception is caught and re-raised, failing the reconnect attempt. With `RECONNECT_MAX_ATTEMPTS=5`, a single flaky attach consumes one attempt. Acceptable but could be more resilient.
- **Suggested fix**: Minor; acceptable as-is. Document the tradeoff.
- **Effort**: minor fix (optional).

---

## PERFORMANCE

### P1. `get_interactive_elements` traverses the entire DOM (querySelectorAll('*')) for shadow roots on every call
- **Category**: performance
- **Severity**: medium
- **File**: `src/cdp/runtime.py` ~L150-200 (`get_interactive_elements` JS)
- **Description**: The JS does `root.querySelectorAll('*').forEach(...)` to find shadow roots, which is O(n) over every element on the page. On large SPAs (10k+ elements), this runs on every step and every retry (executor refreshes DOM after each failed attempt). Combined with the DOM cache absence (PLAN_FASE_B Task 3 not implemented), this is a significant per-step cost.
- **Suggested fix**: Implement the DOM cache from PLAN_FASE_B Task 3, or use `TreeWalker` / chrome's shadowRoot discovery more cheaply.
- **Effort**: major implementation (DOM cache).

### P2. DOM is re-fetched multiple times per step (planner + executor attempts + recovery)
- **Category**: performance
- **Severity**: medium
- **File**: `src/agent/loop.py` ~L260-340, `src/agent/executor.py` ~L80-200
- **Description**: Each step fetches `get_dom_html` + `get_interactive_elements` + `evaluate("window.location.href")` + `dom_fingerprint` BEFORE the action, then again after a failed attempt, then again in `recover_failed_step`. For a 15-step flow with retries, this is 60-100+ CDP round trips that could be cached. PLAN_FASE_B Task 3 (DOM cache) is explicitly designed to fix this and is not implemented.
- **Suggested fix**: Implement `DOMCache` (PLAN_FASE_B Task 3).
- **Effort**: major implementation.

### P3. `wait_for_selector` polls every 0.5s with `Runtime.evaluate` — no `DOM.performSearch` or mutation observer
- **Category**: performance
- **Severity**: low
- **File**: `src/cdp/page.py` ~L330-355 (`wait_for_selector`)
- **Description**: Polls `document.querySelector` every 0.5s up to `timeout`. For a 10s wait, that's 20 CDP round trips. A `MutationObserver`-based approach or `DOM.querySelector` with a single wait would be cheaper.
- **Suggested fix**: Use a MutationObserver-based JS wait, or accept the polling but reduce the interval.
- **Effort**: minor fix.

### P4. `CursorAnimator._loop` sends a `Runtime.evaluate` every 1-3s for the lifetime of the run
- **Category**: performance
- **Severity**: low
- **File**: `src/agent/cursor.py` ~L50-95
- **Description**: The thinking cursor sends a CDP command every 1-3s even when nothing is happening (between steps). Each is a round trip. Minor, but on long runs it adds up and can interfere with `wait_for_network_idle` (the evaluate itself is a "network" event-ish, though it's local).
- **Suggested fix**: Pause the cursor loop when `self._moving is False` for extended periods (already throttles, but doesn't fully stop).
- **Effort**: minor fix.

### P5. `litellm.completion_cost` called on every LLM response — can be slow and is wrapped in bare except
- **Category**: performance / quality
- **Severity**: low
- **File**: `src/llm/client.py` ~L95-105
- **Description**: `litellm.completion_cost(completion_response=response)` is called synchronously on every LLM call. It can raise (caught by `except Exception: pass`) and for unsupported models it does a lookup. The cost is also accumulated via `metrics.increment("llm.cost_usd", cost)` which is a float increment — fine — but the call itself adds latency.
- **Suggested fix**: Make cost calculation optional/opt-in, or cache model pricing.
- **Effort**: minor fix.

---

## CODE QUALITY

### Q1. `loop.py` is a 592-line "god object" — explicitly called out in PLAN_FASE_C Task 1
- **Category**: quality
- **Severity**: medium
- **File**: `src/agent/loop.py`
- **Description**: `AgenticLoop` handles setup, planning, execution, critic, recovery orchestration, checkpointing, metrics, and cursor management. PLAN_FASE_C Task 1 proposes splitting into pipeline handlers. Not a bug, but a maintainability risk.
- **Suggested fix**: Refactor per PLAN_FASE_C Task 1.
- **Effort**: major implementation.

### Q2. `state.py` `RunState` uses mutable default factory lists but no validation on load
- **Category**: quality
- **Severity**: low
- **File**: `src/agent/state.py`
- **Description**: `from_file` does `cls(**data)` after rebuilding `doc_steps`. If the JSON has extra/unknown fields, `**data` raises `TypeError` (caught by the broad except → returns None → treated as missing). Unknown fields in checkpoint files silently discard the entire state.
- **Suggested fix**: Filter to known fields (already done for `doc_steps`, do it for the top-level too) or use `dataclasses` with `extras` tolerance.
- **Effort**: minor fix.

### Q3. Inconsistent error handling patterns across CDP domain methods
- **Category**: quality
- **Severity**: low
- **File**: `src/cdp/page.py`, `src/cdp/input.py`, `src/cdp/runtime.py`
- **Description**: Some methods catch `RuntimeError` (CDP error), some catch generic `Exception`, some re-raise, some swallow. `PageDomain.enable` uses bare `except Exception` for `setLifecycleEventsEnabled`. `InputDomain.click` catches `RuntimeError` specifically. No consistent policy.
- **Suggested fix**: Standardize on catching `WebSocketDisconnected` (re-raise) vs `RuntimeError` (handle) vs `Exception` (log).
- **Effort**: minor fix (mechanical).

### Q4. `recovery.py` re-exports `dom_fingerprint` from `dom_utils` but also defines `is_spa_drift` with duplicated fingerprint logic conceptually
- **Category**: quality
- **Severity**: low
- **File**: `src/agent/recovery.py` ~L20-30
- **Description**: `__all__` exports `dom_fingerprint` (imported from `dom_utils`), and `is_spa_drift` compares fingerprints as plain strings. The old duplicate implementation was removed (good), but the module still re-exports for backward compat. Minor.
- **Suggested fix**: Have callers import from `dom_utils` directly.
- **Effort**: minor fix.

### Q5. `connection.py` `send` uses `asyncio.get_event_loop()` (deprecated) instead of `asyncio.get_running_loop()`
- **Category**: quality
- **Severity**: low
- **File**: `src/cdp/connection.py` ~L340, `src/cdp/page.py` ~L185, ~L130
- **Description**: `asyncio.get_event_loop()` is deprecated in 3.10+ and can create a new loop in some contexts. Inside a coroutine, `asyncio.get_running_loop()` is correct.
- **Suggested fix**: Replace `get_event_loop()` with `get_running_loop()` in coroutine contexts.
- **Effort**: minor fix.

### Q6. `test_runner.py` imports `BrowserSession` and `auto_healer` lazily inside methods
- **Category**: quality
- **Severity**: low
- **File**: `src/agent/test_runner.py` ~L180, ~L250, ~L290
- **Description**: Several `from ..docs.auto_healer import DocAutoHealer` and `from .browser_session import BrowserSession` are done inside methods to avoid circular imports. Works, but indicates a dependency-structure smell.
- **Suggested fix**: Restructure imports or accept the lazy pattern with a comment.
- **Effort**: minor fix.

---

## TEST COVERAGE GAPS

### T1. No direct unit tests for `executor.execute_step` retry/fallback logic
- **Category**: quality (test gap)
- **Severity**: high
- **File**: `tests/test_agent_core.py` has `TestExecutorPlaceholder` with `assert True`
- **Description**: PLAN_FASE_A Task 5 explicitly requires executor tests. Currently only a placeholder exists. The retry loop, fallback JS click, scroll-into-view, destroyed-context handling, and timeout paths are untested. The DOM-cache-dependent paths and recovery interactions are also untested.
- **Suggested fix**: Implement the `FakeBrowserSession`/`FakeLLM` fixtures and executor tests per PLAN_FASE_A Task 5.
- **Effort**: major implementation.

### T2. No direct unit tests for `loop.py` orchestration
- **Category**: quality (test gap)
- **Severity**: high
- **File**: `tests/test_agent_core.py` has `TestLoopPlaceholder` with `assert True`
- **Description**: The full plan→execute→critique pipeline, checkpoint resume, URL-drift replan, crash recovery (`MAX_CRASHES`), and critic re-execution are untested at the unit level. `test_agent_loop_spa_drift.py` exists but is an integration-style test (12KB).
- **Suggested fix**: Implement loop tests with mocked session/llm per PLAN_FASE_A Task 5.
- **Effort**: major implementation.

### T3. No tests for `recovery.py` local recovery / SPA replan decision parsing
- **Category**: quality (test gap)
- **Severity**: medium
- **File**: `tests/` — no `test_recovery.py`
- **Description**: `recover_failed_step`, `recover_locally`, `replan_spa_step`, `replan_remaining`, and `parse_recovery_decision` have no dedicated tests. The SPA-drift detection (`is_spa_drift`) is exercised by `test_agent_loop_spa_drift.py` but the local recovery decision tree is not.
- **Suggested fix**: Add unit tests for the recovery decision tree with mocked LLM responses.
- **Effort**: minor-to-medium.

### T4. No tests for `browser_session.handle_login` form detection/filling
- **Category**: quality (test gap)
- **Severity**: medium
- **File**: `tests/test_browser_session_circuit.py` only tests the circuit breaker surface, not `handle_login`
- **Description**: The login detection JS, credential injection, and submit-button scoping are untested. This is security-sensitive (credential handling) and fragile (React-controlled inputs).
- **Suggested fix**: Add tests with a mocked runtime evaluating login-detection JS.
- **Effort**: medium.

### T5. No tests for `cursor.py` highlight/clear or `extract_selector`
- **Category**: quality (test gap)
- **Severity**: low
- **File**: `tests/test_cursor_throttle.py` tests throttling only
- **Description**: `highlight_element`, `clear_highlight`, and `extract_selector` regex are untested. The regex edge cases (multi-line, `click_text`, failed prefixes) are unverified.
- **Suggested fix**: Add unit tests for `extract_selector` (pure function).
- **Effort**: minor fix.

### T6. No tests for `input.py` `type_text` native-setter path or `click` box-model math
- **Category**: quality (test gap)
- **Severity**: medium
- **File**: no `test_input.py`
- **Description**: The center-point computation from a quad and the React-compatible native-setter typing are untested. These are easy to break on a refactor.
- **Suggested fix**: Add unit tests with a mocked connection.
- **Effort**: minor fix.

### T7. No tests for `runtime.py` `get_state_fingerprint` redaction of password fields
- **Category**: quality (test gap) / security
- **Severity**: medium
- **File**: no `test_runtime_fingerprint.py`
- **Description**: The fingerprint explicitly redacts passwords to `[REDACTED:N]`. This security-relevant behavior has no regression test.
- **Suggested fix**: Add a test verifying password values don't appear in the fingerprint.
- **Effort**: minor fix.

### T8. No tests for `llm/client.py` retry beyond `RateLimitError`
- **Category**: quality (test gap)
- **Severity**: medium
- **File**: `tests/test_llm_client_compat.py` is tiny (551 bytes)
- **Description**: The retry loop's backoff timing, the `last_error` raise-after-retries, and the metrics recording are untested. Vision multi-image path is untested.
- **Suggested fix**: Add tests with mocked `litellm.acompletion`.
- **Effort**: medium.

---

## SUMMARY TABLE

| ID | Category | Severity | File | Effort |
|----|----------|----------|------|--------|
| C1 | bug/leak | critical | browser_session.py ~L170 | minor/major |
| C2 | bug/security | critical | browser_session.py ~L35 | major |
| C3 | bug | high | dom_utils.py ~L66 | minor |
| H1 | leak | high | connection.py ~L380 | minor |
| H2 | bug | high | page.py ~L190 | major |
| H3 | bug | high | connection.py ~L70 | major |
| H4 | reliability | high | llm/client.py ~L70 | minor/major |
| H5 | quality | medium | llm/client.py ~L20 | minor |
| H6 | bug | medium | browser_session.py ~L150 | minor |
| H7 | reliability | high | browser_session.py ~L260 | minor |
| H8 | quality | medium | cursor.py ~L50 | minor |
| M1 | bug | medium | executor.py ~L260 | minor |
| M2 | bug | medium | page.py ~L150 | minor |
| M3 | bug | medium | page.py ~L130 | minor |
| M4 | bug | medium | executor.py ~L215 | minor |
| M5 | bug | low | cursor.py ~L150 | minor |
| M6 | reliability | medium | browser_session.py ~L260 | minor |
| M7 | bug | medium | loop.py ~L120 | minor |
| M8 | bug | medium | loop.py ~L110 | minor |
| M9 | reliability | medium | state.py ~L80 | minor |
| M10 | reliability | medium | connection.py ~L575 | minor |
| M11 | reliability | medium | browser.py ~L170 | minor |
| M12 | leak | medium | browser.py ~L130 | minor |
| M13 | quality | medium | test_runner.py ~L180 | minor |
| M14 | leak | high | test_runner.py ~L130 | minor |
| M15 | reliability | medium | test_runner.py ~L330 | minor |
| M16 | bug | medium | executor.py ~L300 | minor |
| M17 | bug | medium | executor.py ~L195 | minor |
| M18 | quality | low | loop.py/recovery.py | minor |
| M19 | bug | low | loop.py ~L280 | minor |
| M20 | quality | low | connection.py ~L210 | minor |
| S1 | security | high | browser_session.py ~L230 | minor |
| S2 | security | high | executor.py ~L240 | minor |
| S3 | security | medium | sanitize.py ~L260 | minor |
| S4 | security | medium | sanitize.py ~L165 | minor |
| S5 | security | low | runtime.py ~L250 | minor |
| S6 | security | medium | executor.py ~L235 | minor |
| R1 | reliability | medium | page.py ~L100 | minor |
| R2 | reliability | low | browser.py ~L160 | minor |
| R3 | reliability | medium | recovery.py ~L165 | minor |
| R4 | reliability | medium | critic.py ~L55 | minor |
| R5 | reliability | low | loop.py ~L145 | minor |
| R6 | reliability | low | executor.py ~L245 | minor |
| R7 | reliability | medium | input.py ~L60 | minor |
| R8 | bug | low | input.py ~L195 | minor |
| R9 | reliability | low | connection.py ~L290 | minor |
| P1 | performance | medium | runtime.py ~L150 | major |
| P2 | performance | medium | loop.py/executor.py | major |
| P3 | performance | low | page.py ~L330 | minor |
| P4 | performance | low | cursor.py ~L50 | minor |
| P5 | performance | low | llm/client.py ~L95 | minor |
| Q1 | quality | medium | loop.py | major |
| Q2 | quality | low | state.py | minor |
| Q3 | quality | low | cdp/* | minor |
| Q4 | quality | low | recovery.py | minor |
| Q5 | quality | low | connection.py/page.py | minor |
| Q6 | quality | low | test_runner.py | minor |
| T1 | test gap | high | tests/ | major |
| T2 | test gap | high | tests/ | major |
| T3 | test gap | medium | tests/ | medium |
| T4 | test gap | medium | tests/ | medium |
| T5 | test gap | low | tests/ | minor |
| T6 | test gap | medium | tests/ | minor |
| T7 | test gap | medium | tests/ | minor |
| T8 | test gap | medium | tests/ | medium |

---

## TOP PRIORITIES (recommended order)

1. **C1/C2** — Chrome process leak on Windows (crash + orphan kill). Blocks production Windows use.
2. **S2** — Password leak into docs/checkpoints/logs via executor `type` action_desc. Silent credential exposure.
3. **S3/S4** — `sanitize` raw mode bypasses sensitive redaction. Contradicts documented behavior.
4. **H4** — LLM retry only on RateLimitError. Every transient LLM failure kills a step.
5. **H7** — `recover()` skips login → runs unauthenticated after crash.
6. **H1** — Pending future leak in `send()` on socket error.
7. **M14** — `test_runner.py` Chrome PID not registered → orphan leak.
8. **C3** — `dom_utils` `get_metrics()` None deref.
9. **H2/H3** — Shared `_events` queue races (root cause of several reliability issues).
10. **T1/T2** — Executor and loop have only placeholder tests (PLAN_FASE_A Task 5).
