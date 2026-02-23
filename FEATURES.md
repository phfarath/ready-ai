# Features

## 🧠 Pipeline Agentic (Planner → Executor → Critic)

- **Planner** — LLM analisa o DOM e gera plano de passos numerados para documentar o fluxo
- **Executor** — Converte cada passo em ação CDP (click, type, navigate, scroll, observe)
- **Critic** — Revisa a documentação gerada, pontua (1-10) e identifica passos faltantes
- **Annotator** — Vision LLM analisa cada screenshot e gera descrição contextual do que o usuário vê na língua do goal

## 🔧 CDP Engine (Chrome DevTools Protocol)

- Comunicação raw via WebSocket (sem Playwright/Selenium)
- Auto-incremento de message IDs e roteamento de eventos
- Lançamento automático do Chrome com `--remote-debugging-port`
- Detecção multiplataforma do binário (macOS, Linux) + suporte a `CHROME_PATH`
- Domínios implementados:
  - **Page** — navigate, screenshot (full-page), get_dom_html (truncado para LLM), wait_for_selector
  - **Input** — click via `getBoxModel` + `dispatchMouseEvent`, type char-a-char, press_key, scroll
  - **Runtime** — evaluate JS, query_selector, get_visible_text, get_interactive_elements

## ✅ Verificação Pós-Ação (Gap 1)

- `StepResult` dataclass rastreia sucesso, tentativas e motivo de falha
- Comparação de DOM (hash MD5 do texto visível completo + URL) antes/depois de cada ação
- Retry loop automático (máx. 3 tentativas) com contexto de falha no re-prompt
- Estratégias de fallback em sequência:
  1. Scroll into view antes de re-clicar
  2. JS click direto (`el.click()`)
  3. Click por texto visível (`click_text`)
- Passos falhados marcados como `[FAILED]` na documentação final

## 🔄 Critic com Re-execução (Gap 2)

- Critic retorna `missing_steps` como dados estruturados
- Passos faltantes são enviados ao Planner via `PLANNER_SUPPLEMENT_SYSTEM`
- Planner gera sub-plano apenas para os gaps
- Executor re-executa os passos adicionais e appenda na documentação
- **Resultados acumulados** — `StepResult` dos re-executed steps são integrados ao loop principal

## 🔐 Autenticação (Gap 3)

- **Cookies** — `--cookies-file` injeta cookies de sessão via `Network.setCookie` (formato JSON exportável por extensões como EditThisCookie)
- **Login automático** — `--username` / `--password` auto-detecta formulários de login (campos email/password), preenche e submete
- **Escape seguro** — credenciais são escapadas via `json.dumps()` para compatibilidade com senhas contendo `'`, `\`, `"`
- Eventos `input` e `change` disparados para compatibilidade com frameworks React/Vue

## 🎯 Resolução Robusta de Elementos (Gap 4)

- `get_interactive_elements()` expõe 9 atributos por elemento: tag, text, id, name, ariaLabel, role, testId, visible, inShadowDom, inIframe
- **Shadow DOM** — traversal recursivo de `shadowRoot` para encontrar elementos em Web Components
- **Iframes** — traversal de `iframe.contentDocument` (same-origin)
- Seletor mais estável computado automaticamente com hierarquia de prioridade:
  1. `aria-label` / `role` + texto
  2. `data-testid` / `data-cy`
  3. `#id`
  4. `[name="..."]`
  5. CSS semântico
  6. CSS posicional (evitado)
- Prompt do Executor instrui explicitamente a priorizar seletores estáveis
- `EXECUTOR_RETRY_SYSTEM` sugere estratégias diferentes a cada tentativa
- `find_element_by_text()` no RuntimeDomain como último recurso
- Dedup de elementos via `WeakSet` (referência de objeto) — sem colisões em botões duplicados

## 💰 Separação de Modelo de Anotação (Gap 5)

- `--annotation-model` permite usar modelo mais barato para anotações de screenshot
- Planner e Critic usam o `--model` principal (ex: `claude-sonnet-4-20250514`)
- Anotações via vision usam modelo separado (ex: `gpt-4o-mini`) para reduzir custo

## 🌐 Consciência de Navegação (v2.1)

- **URL tracking** — após cada step, URL atual é capturada e injetada no prompt do Executor
- **Drift detection** — warning automático quando URL muda entre steps (redireccionamentos, SPAs)
- **Replanning automático** — quando URL muda inesperadamente, steps restantes são re-planejados via `PLANNER_REPLAN_SYSTEM`
- O Executor sabe em qual página está, evitando executar ações na página errada

## 📸 Visual Highlighting (v2.1)

- Elemento interagido recebe highlight visual (borda vermelha + sombra) antes do screenshot
- Highlight é removido após captura para não afetar steps subsequentes
- Graceful degradation — falha no highlight não bloqueia o fluxo
- Selector extraído automaticamente do `action_desc` do Executor

## 🔒 Event Safety (v2.1)

- `wait_for_event` agora bufferiza eventos CDP não-matching e re-enfileira após match/timeout
- Eventos críticos como `Page.frameNavigated` não são mais descartados permanentemente
- Previne travamentos silenciosos em SPAs com alto volume de eventos CDP

## 🌍 Consistência de Idioma nas Anotações (v2.3)

- `ANNOTATOR_PROMPT` instrui o LLM a escrever **na língua do GOAL**, não na do texto da UI visível no screenshot
- Resolve drift de idioma em apps com UI em português quando o goal está em inglês (e vice-versa)
- Placeholder `{goal}` passado junto com `{step}` na chamada vision — o modelo sempre ancora no idioma do goal

## 📝 Flag `--title` para Título Limpo (v2.3)

- `--title` / `-t` permite definir um título H1 separado do `--goal`
- O `--goal` continua sendo a instrução que dirige o LLM (pode ser longa e descritiva)
- O `--title` vira o cabeçalho H1 do documento gerado (curto e legível)
- Retrocompatível: sem `--title`, o H1 usa o `--goal` como antes

## 📄 Geração de Documentação

- Markdown com Table of Contents automático
- Screenshot por passo salvo como PNG individual
- Anotações contextuais geradas via vision LLM
- Detalhes técnicos em blocos `<details>` colapsáveis
- Seção de notas do Critic com sugestões de melhoria
- Output portável: `docs.md` + diretório `screenshots/`

## 🖥️ CLI

```
--goal, -g          Objetivo da documentação (instrução para o LLM)
--url, -u           URL alvo
--title, -t         Título H1 do documento gerado (default: goal)
--model, -m         Modelo LLM principal (default: gpt-4o-mini)
--annotation-model  Modelo para anotações de screenshot
--output, -o        Diretório de saída
--port, -p          Porta de debug do Chrome (default: 9222)
--headless          Modo headless
--max-critic-rounds Rodadas máximas do Critic (default: 2)
--language, -l      Idioma da documentação gerada (ex: English, Portuguese, pt, es, fr, de, it)
--cookies-file      Arquivo JSON de cookies para autenticação
--username          Username/email para login automático
--password          Senha para login automático
--verbose, -v       Logging detalhado
```

## 🌐 Flag `--language` para Documentação Multilíngue (v2.4)

- `--language` / `-l` — flag única que controla o idioma de **toda** a saída:
  - **Labels do renderer** (`## Step`, `## Index`, `Technical details`, etc.) — via dict `_LABELS` em `renderer.py`
  - **Texto gerado pelo LLM** (anotações, passos planejados) — via instrução explícita nos prompts
- Idiomas suportados com labels pré-definidos: **English** (default), **Portuguese**, **Spanish**, **French**, **German**, **Italian**
- Aceita nome completo (`Portuguese`) ou código ISO-2 (`pt`) — desconhecidos fazem fallback para inglês
- Sem `--language`, o default é inglês; o idioma do goal ainda ancora as anotações via `{goal}` no `ANNOTATOR_PROMPT`

## 🔌 Multi-Provider LLM

- Suporte via LiteLLM a qualquer provider: OpenAI, Anthropic, Google, Mistral, etc.
- `.env` carregado automaticamente via `python-dotenv`
- JSON mode para outputs estruturados (Executor, Critic)
- Vision API para análise de screenshots (base64)
- Retry automático com backoff exponencial em rate limit (máx. 5 tentativas)
