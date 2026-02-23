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

---

# Bug Fixes (v2.1) ✅

## Bug 1: Credential Escaping ✅
- [x] Fix `_handle_login` JS injection — usar `json.dumps()` para escapar username/password
- [x] Fix `find_element_by_text` JS injection — escapar text e tag_filter
- [x] Fix `click_text` JS injection — escapar text via `json.dumps()`

## Bug 2: Event Buffering em `wait_for_event` ✅
- [x] Stash eventos non-matching durante `wait_for_event`
- [x] Re-queue eventos stashed via `try/finally` após match ou timeout
- [x] Prevenir perda de eventos críticos como `Page.frameNavigated`

## Bug 3: Verificação Pós-Ação Completa ✅
- [x] Substituir comparação truncada (500 chars) por hash MD5 do texto completo
- [x] Detectar mudanças abaixo do fold, modais, toasts

## Bug 4: Dedup de Elementos por Referência ✅
- [x] Substituir `Set` com string key por `WeakSet` com referência de objeto
- [x] Eliminar colisões em botões com mesmo texto ("OK", "×")

## Bug 5: Acumular StepResults no Critic Loop ✅
- [x] `_reexecute_missing_steps` retorna `list[StepResult]`
- [x] `_critic_loop` acumula resultados via `step_results.extend()`

---

# Structural Improvements (v2.1) ✅

## Gap A: Consciência de URL Entre Steps ✅
- [x] Capturar URL atual após cada step em `_execute_steps`
- [x] Passar `current_url` para `execute_step` e incluir no prompt
- [x] Log de warning quando URL muda entre steps (navigation drift)

## Gap B: Suporte a Shadow DOM e Iframes ✅
- [x] Traversal recursivo de `shadowRoot` em `get_interactive_elements()`
- [x] Traversal de `iframe.contentDocument` (same-origin)
- [x] Anotar elementos com `inShadowDom` e `inIframe` flags

## Gap C: Visual Highlighting nas Screenshots ✅
- [x] Extrair selector do `action_desc` via regex
- [x] Aplicar highlight (red border + box-shadow) antes do screenshot
- [x] Limpar highlight após captura
- [x] Graceful degradation — falha no highlight não bloqueia screenshot
