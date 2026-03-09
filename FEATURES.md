# Features

## Estado Atual

ready-ai ja entrega dois modos principais:

- **CLI local** para gerar documentacao a partir de `goal + url`
- **API HTTP experimental** para disparar runs em background, acompanhar status e baixar artefatos

O projeto esta na fase de **CLI hardening + API stabilization**.

## Pipeline Agentic

- **Planner**: analisa DOM + elementos interativos e produz a lista de passos
- **Executor**: converte cada passo em acao CDP, verifica efeito no DOM e tenta recuperacao quando falha
- **Critic**: revisa o documento gerado, pontua qualidade e pede reexecucao de gaps
- **Annotator**: usa vision LLM para descrever cada screenshot no idioma desejado

## Engine CDP

- WebSocket raw com JSON-RPC, sem Playwright ou Selenium
- Lancamento automatico do Chrome com `--remote-debugging-port`
- Deteccao de binario por plataforma e suporte a `CHROME_PATH`
- Dominios implementados:
  - `Page`: navegacao, screenshot, DOM, espera por page load e rede ociosa
  - `Input`: click, type, key press e scroll
  - `Runtime`: evaluate, busca de elementos e texto visivel

## Robustez de Execucao

- Verificacao pos-acao com fingerprint do DOM + URL
- Retry por step com ate 3 tentativas
- Fallbacks progressivos: scroll into view, JS click e click por texto
- Replanejamento automatico quando a URL muda entre steps
- Deteccao de drift em SPA quando a URL nao muda mas o estado muda
- Highlight visual do elemento antes da captura do screenshot

## Cobertura de Interface

- Traversal recursivo de Shadow DOM
- Traversal de iframes same-origin
- Seletores estaveis priorizando `aria-label`, `data-testid`, `id` e `name`
- Deduplicacao por referencia de objeto para evitar colisao entre elementos parecidos

## Autenticacao

- Injecao de cookies via `--cookies-file`
- Auto-login com `--username` e `--password`
- Escape seguro de strings JS com `json.dumps()`
- Compatibilidade com formulários React/Vue disparando eventos nativos

## Saida

- `docs.md` + `screenshots/`
- indice automatico
- detalhes tecnicos em blocos colapsaveis
- `--title` para separar H1 da instrucao do LLM
- `--language` para controlar labels do renderer e texto dos prompts
- `--annotation-model` para otimizar custo de vision

## Estado e Recuperacao

- Checkpoint por run em JSON
- Resume de runs interrompidos
- Rastreamento de `current_step_index`, `planned_steps`, `executed_results` e `last_known_url`

## API HTTP

- `POST /runs` cria um run
- `GET /runs/{run_id}` retorna status
- `GET /runs/{run_id}/output` baixa o ZIP do output
- Reuso de `run_id` permite retomar runs com checkpoint existente

## CLI

Comandos disponiveis:

```text
ready-ai run ...
ready-ai api ...
```

Flags principais do `run`:

```text
--goal, -g
--url, -u
--title, -t
--language, -l
--model, -m
--annotation-model
--output, -o
--port, -p
--headless
--max-critic-rounds
--cookies-file
--username
--password
--verbose, -v
```

## Gaps Abertos

- Estabilizacao da stack de API e testes
- Melhor tratamento para steps `[FAILED]`
- `--config` em YAML/TOML
- dry-run / plan-only
- OAuth / SSO
- MFA / TOTP
- multi-tab
- cross-origin iframe
