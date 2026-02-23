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

---

# Output Quality Fixes (v2.3) ✅

## Fix 1: Annotation Language Consistency ✅
- [x] Add `{goal}` placeholder to `ANNOTATOR_PROMPT`
- [x] Change language rule to "write in the same language as the GOAL, not the UI text"
- [x] Pass `goal=self.goal` alongside `step=step` at the call site in `loop.py`

## Fix 2: `--title` Flag for Clean Document Heading ✅
- [x] Add `--title` / `-t` optional CLI argument in `main.py`
- [x] Add `title: Optional[str] = None` parameter to `AgenticLoop.__init__`
- [x] Pass `title=self.title` to `DocRenderer` instantiation
- [x] `DocRenderer.__init__` accepts `title=None`; `render()` uses `self.title or self.goal` for H1

---

# Multilingual Output (v2.4) ✅

## `--language` Flag ✅
- [x] Add `--language` / `-l` CLI flag (accepts full name or 2-letter ISO code)
- [x] Add `_LABELS` dict in `renderer.py` with pre-defined labels for English, Portuguese, Spanish, French, German, Italian
- [x] Add `_LANG_ALIASES` for 2-letter code resolution; unknown languages fall back to English
- [x] `DocRenderer.__init__` accepts `language=`; resolves labels at construction time
- [x] `ANNOTATOR_PROMPT`: replace hardcoded language rule with `{language_instruction}` placeholder
- [x] `loop.py`: compute `language_instruction` as `"Write in {language}"` when set, goal-anchor fallback when not
- [x] `planner.plan()` accepts `language=`; appends `IMPORTANT: Write all output in {language}` to user prompt
- [x] `loop.py _replan_remaining` and `_reexecute_missing_steps`: append same language instruction
- [x] Tests updated to English default; added `test_render_portuguese_labels` and `test_render_language_alias`

---

# Roadmap — Open Items

## Output Quality
- [ ] Configurable output template — allow users to supply a Jinja2/Markdown template for the output format
- [ ] Configurable output template — allow users to supply a Jinja2/Markdown template for the output format
- [ ] Better failed-step recovery — skip failed steps rather than embedding `[FAILED]` inline, or provide a `--retry-failed` mode

## Robustness
- [ ] Cross-origin iframe support — current iframe traversal is limited to same-origin
- [ ] Multi-tab support — some SaaS apps open new tabs for OAuth or modals
- [ ] Network idle detection — replace `asyncio.sleep` settle waits with `Page.networkIdle` or `Page.lifecycleEvent`

## Auth
- [ ] OAuth / SSO flows — current auto-login only handles email+password forms
- [ ] MFA / TOTP support — accept a TOTP secret or callback for 2FA apps

## Developer Experience
- [ ] `--config` file support — read all CLI options from a YAML/TOML config file
- [ ] Dry-run / plan-only mode — print the planned steps without executing them
- [ ] Resume from checkpoint — save progress after each step; resume on crash
