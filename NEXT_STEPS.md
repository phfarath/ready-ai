# Next Steps — Critical Gap Fixes

## Gap 1: Executor Post-Action Verification ✅
- [x] Create `StepResult` dataclass with `success`, `retry_needed`, `action_desc`
- [x] Implement DOM diff check (visible text + URL before/after action)
- [x] Add retry loop per step with failure context in prompt (max 3 attempts)
- [x] Add fallback strategies: scroll into view, JS click, XPath
- [x] Mark failed steps as `[FAILED]` in docs instead of silently passing

## Gap 2: Critic Re-execution Loop ✅
- [x] Critic returns `missing_steps` as structured data
- [x] Loop.py passes missing steps back to Planner as additional context
- [x] Planner generates sub-plan for missing steps (`PLANNER_SUPPLEMENT_SYSTEM`)
- [x] Executor re-executes missing steps
- [x] DocRenderer appends re-executed steps

## Gap 3: Authentication Support ✅
- [x] Add `--cookies-file` CLI flag to load session cookies
- [x] Add `--username` / `--password` CLI flags
- [x] Implement CDP cookie injection (`Network.setCookie`)
- [x] Implement login form auto-detection and fill
- [x] Detect login redirect mid-execution and handle gracefully

## Gap 4: Robust Element Resolution ✅
- [x] Upgrade `get_interactive_elements()` to expose `aria-label`, `role`, `data-testid`, `data-cy`, `visible`
- [x] Update `EXECUTOR_SYSTEM` prompt selector priority: aria-label > data-testid > id > name > semantic > positional
- [x] Add `click_text` action type for text-based clicking as fallback
- [x] Add `scroll_to` action for scrolling elements into view 
- [x] Add `find_element_by_text()` in RuntimeDomain
- [x] Add `EXECUTOR_RETRY_SYSTEM` prompt with different strategy suggestions

## Gap 5: Annotation Model Separation ✅
- [x] Add `--annotation-model` CLI flag (defaults to main model)
- [x] Pass separate model config through `AgenticLoop` to vision calls
- [x] Allow cheaper/faster model for annotation while keeping better model for planning/criticism
