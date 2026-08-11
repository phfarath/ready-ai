# Plano Técnico Detalhado — Fase A: Segurança & Robustez

> **Projeto:** ready-ai  
> **Foco:** Corrigir vulnerabilidades críticas de segurança e eliminar fugas de processos/zombies antes de qualquer refactor de features.  
> **Princípio:** Nenhuma task depende de outra não-concluída salvo onde explicitamente indicado.

---

## 1. Resumo dos Problemas Identificados

| # | Problema | Arquivo(s) Afetados | Severidade |
|---|----------|---------------------|------------|
| 1 | **API sem autenticação** — qualquer cliente pode criar runs, exportar dados e listar histórico. | `src/api/server.py`, `src/api/models.py` | 🔴 Crítica |
| 2 | **Chrome process vira zombie** — `teardown()` usa `terminate()`/`kill()` sem garantir que o processo realmente morreu; não há coleta de subprocessos órfãos. | `src/agent/browser_session.py` | 🔴 Crítica |
| 3 | **Sem graceful shutdown** — SIGTERM/SIGINT não são capturados; tasks asyncio e browsers continuam rodando ao reiniciar o container/process manager. | `src/api/server.py`, `src/api/manager.py` | 🟠 Alta |
| 4 | **Rate limit in-memory** — `_rate_limit_store` é um dict global que não replica entre workers/processos; inviável para deployments multi-worker (gunicorn/uvicorn). | `src/api/server.py` | 🟡 Média |
| 5 | **Agent core sem testes diretos** — `loop.py` (592 linhas), `executor.py` (440 linhas) e `browser_session.py` (367 linhas) possuem zero testes unitários diretos. | `tests/` (ausente) | 🟠 Alta |

---

## 2. Arquitetura das Soluções

### 2.1 Autenticação da API (Task 1)

**Abordagem:** Token-based simples via header `X-API-Key`, validado em middleware FastAPI.

- **Por que não OAuth/JWT complexo?** O projeto é single-tenant e self-hosted; a sobrecarga de JWT com refresh tokens é over-engineering para o estágio atual.
- **Por que não Basic Auth?** Tokens de API são mais fáceis de rotacionar e logar de forma segura (sem expor senhas em headers codificados em base64).

**Arquitetura:**

```
┌─────────────────────┐
│  Cliente (curl/UI)  │─── Header: X-API-Key: <token>
└─────────────────────┘
          │
          ▼
┌──────────────────────────────────────────┐
│  FastAPI Middleware (APIKeyHeader)       │
│  - Valida token contra lista autorizada  │
│  - Rate limit separado por token (task 4)│
│  - Retorna 401/403 antes de qualquer     │
│    lógica de negócio                     │
└──────────────────────────────────────────┘
```

- Tokens são armazenados em variável de ambiente `READY_API_KEYS` (lista separada por vírgula).
- Nenhum endpoint fica aberto exceto `/health` e `/ready` (necessários para load balancers).
- O middleware será aplicado globalmente e pode ser desabilitado em modo `dev` via `AUTH_DISABLED=true`.

---

### 2.2 Prevenção de Zombie Chrome + Graceful Shutdown (Tasks 2 e 3)

**Abordagem:** Triple-safeguard no teardown + signal handlers no processo principal.

**Safeguard 1 — Teardown Elegante:**
1. Tentativa de `proc.terminate()` → `proc.wait(timeout=3)`.
2. Se ainda vivo: `proc.kill()` → `proc.wait(timeout=3)`.
3. Se ainda vivo após kill: logging crítico + registro do PID para coleta externa.

**Safeguard 2 — Signal Handlers no Processo Principal:**
- Capturar `SIGTERM`, `SIGINT`, `SIGHUP`.
- Cancelar todas as tasks asyncio pendentes via `asyncio.gather(*tasks, return_exceptions=True)`.
- Invocar `RunManager.shutdown_all()` que itera por todos os runs ativos chamando `browser_session.teardown()`.
- Fechar event loop de forma limpa.

**Safeguard 3 — Coleta de Órfãos (Windows-safe):**
- Em Windows: usar `psutil` para encontrar subprocessos do Chrome cujo PPID corresponde ao Python morto.
- Registrar todos os PIDs de Chrome lançados em um registry thread-safe (`weakref.WeakSet` ou `set` protegido por lock).
- No signal handler, iterar o registry e forçar terminação de qualquer Chrome órfão.

**Arquitetura:**

```
┌──────────────────┐     SIGTERM/SIGINT      ┌──────────────────────┐
│  OS / Docker     │────────────────────────>│  Signal Handler      │
│  (systemd/k8s)   │                         │  (main thread)       │
└──────────────────┘                         └──────────────────────┘
                                                      │
                    ┌─────────────────────────────────┼─────────────────────────────────┐
                    ▼                                 ▼                                 ▼
         ┌────────────────────┐          ┌────────────────────┐          ┌────────────────────┐
         │ asyncio.Task.cancel│          │ RunManager.stop_all│          │ PID Registry       │
         │ (all pending)      │          │ (teardown sessions)│          │ (kill orphans)     │
         └────────────────────┘          └────────────────────┘          └────────────────────┘
```

**Nota sobre o loop `run()` de `AgenticLoop`:**
- Adicionar um flag `_shutdown_requested: asyncio.Event` na classe.
- O signal handler seta esse evento.
- O loop verifica periodicamente (a cada step) se o evento está setado; se sim, salva estado e sai limpo.

---

### 2.3 Rate Limit Distribuído (Task 4)

**Abordagem:** Redis como backend de rate limit, com fallback graceful para in-memory se Redis não estiver disponível.

**Por que Redis?**
- Única alternativa viável para multi-worker sem estado compartilhado.
- O projeto já pode ter Redis no stack futuro (jobs/batch); agora é o momento de introduzi-lo.
- Se não houver Redis, o rate limit continua funcionando (fallback in-memory) com um warning no log.

**Arquitetura:**

```
┌──────────────┐      ┌─────────────────┐      ┌──────────────┐
│   Request    │─────>│  FastAPI        │─────>│  Redis       │
│   (IP/Token) │      │  Middleware     │      │  (TTL-based) │
└──────────────┘      │  - Lua scripts  │      │  key: rl:<id>│
                      │    atomic incr  │      │  window: 60s │
                      └─────────────────┘      └──────────────┘
```

**Implementação:**
- Usar `redis-py` com scripts Lua atômicos (`INCR` + `EXPIRE`).
- Chave: `rate_limit:{client_id}` onde `client_id` = token (se autenticado) ou IP (se anônimo em dev).
- Config via env: `REDIS_URL` (default: None → fallback in-memory).
- Criar abstração `RateLimiter` (protocolo) com duas implementações: `RedisRateLimiter` e `InMemoryRateLimiter`.

---

### 2.4 Testes Diretos no Agent Core (Task 5)

**Abordagem:** Testes unitários focados em comportamentos críticos (não em E2E), usando mocks pesados para evitar carregar Chrome/LLM.

**Estratégia por arquivo:**

| Arquivo | Foco dos Testes | Mockar |
|---------|-----------------|--------|
| `browser_session.py` | `setup()`, `teardown()`, `inject_cookies()`, `recover()` | `launch_chrome`, `get_ws_url`, `CDPConnection` |
| `executor.py` | `execute_step()`, retry logic, verificação pós-ação | `BrowserSession`, `LLMClient` |
| `loop.py` | Orquestração: planner → executor → critic, estado de RunState, graceful stop | Tudo acima + `DocRenderer`, `save_docs` |

**Arquitetura dos testes:**

```
┌──────────────────────────────────────────────────────────┐
│  tests/unit/agent/                                       │
│    ├── test_browser_session.py   (mocks cdp/)            │
│    ├── test_executor.py          (mocks browser + llm) │
│    └── test_loop.py              (mocks tudo acima)      │
└──────────────────────────────────────────────────────────┘
```

**Convenções:**
- Usar `unittest.mock.AsyncMock` para todas as corotinas.
- Criar fixtures `mock_browser_session`, `mock_llm_client` em `conftest.py`.
- Nenhum teste unitário lança Chrome real. Testes que precisam de Chrome real são E2E e ficam em `tests/e2e/`.

---

## 3. Tasks Ordenadas por Dependência

```
[Task 1] Auth Middleware      ──┐
[Task 2] Graceful Shutdown   ──┤──> independentes, podem ser feitos em paralelo
[Task 3] Zombie Prevention   ──┤
[Task 4] Rate Limit Redis    ───┘ (depende parcialmente de Auth para usar token como key)
                                 │
                                 ▼
[Task 5] Tests Agent Core    ──── (depende de 1-4 estar estável para usar infra real como refs)
```

**Observação:** Tasks 1-4 são implementações independentes e podem ser desenvolvidas em paralelo por devs diferentes. Task 5 deve vir por último para que os testes validem a infraestrutura já estável.

---

### Task 1: Middleware de Autenticação API Key
- **Complexidade:** Média
- **Riscos:**
  - Quebrar scripts existentes de CI/CD que chamam a API sem token.
  - Mitigação: documentar variável `AUTH_DISABLED=true` para ambiente dev/CI.
- **Arquivos criados:**
  - `src/api/auth.py` — middleware `APIKeyHeader`, validação de token, anotação `RequireAuth`.
  - `src/api/dependencies.py` — dependência injetável `get_current_token`.
- **Arquivos modificados:**
  - `src/api/server.py` — adicionar middleware de auth global; adicionar `X-API-Key` ao schema OpenAPI.
  - `src/api/models.py` — adicionar campo `api_key` aos modelos de request (ou usar header exclusivo).
  - `tests/test_api.py` — adicionar headers de auth em todos os testes; testar 401/403.
  - `tests/test_api_batch.py` — idem.
  - `tests/test_api_requests.py` — idem.
  - `tests/test_api_phase3.py` — idem.
- **Critérios de aceitação:**
  - [ ] POST /runs sem header `X-API-Key` retorna 401.
  - [ ] Token inválido retorna 403.
  - [ ] Token válido permite operação.
  - [ ] `/health` e `/ready` permanecem públicos.
  - [ ] `AUTH_DISABLED=true` desabilita validação.

---

### Task 2: Graceful Shutdown (SIGTERM / SIGINT)
- **Complexidade:** Média
- **Riscos:**
  - Deadlock ao cancelar tasks que estão em `await` de I/O bloqueante.
  - Mitigação: usar `asyncio.timeout` em todos os awaits do shutdown; logar e prosseguir se timeout.
- **Arquivos criados:**
  - `src/api/lifecycle.py` — funções `register_signal_handlers()`, `shutdown_event_loop()`, `cleanup_resources()`.
- **Arquivos modificados:**
  - `src/api/server.py` — chamar `register_signal_handlers()` no startup; adicionar lifespan/startup event.
  - `src/api/manager.py` — adicionar método `async def shutdown_all() -> None` que itera `_runs` cancelando tasks e chamando teardown.
  - `src/agent/loop.py` — adicionar `asyncio.Event _shutdown_requested`; verificar no loop principal.
  - `src/agent/browser_session.py` — garantir que `teardown()` seja idempotente (múltiplas chamadas seguras).
- **Critérios de aceitação:**
  - [ ] Enviar SIGTERM ao processo uvicorn faz com que todos os runs ativos chamem `teardown()`.
  - [ ] Runs em execução salvam estado antes de morrer.
  - [ ] Nenhuma task asyncio fica pendente ao fim do shutdown.
  - [ ] Timeout total de shutdown ≤ 30s.

---

### Task 3: Prevenção de Chrome Zombies e Fuga de Processos
- **Complexidade:** Alta
- **Riscos:**
  - Em Windows, `proc.kill()` pode não matar subprocessos do Chrome (renderer, GPU, etc.).
  - Mitigação: usar `psutil` para iterar árvore de processos e matar TODOS os filhos do Chrome.
  - Race condition entre `teardown()` e novo `setup()` com mesmo port.
  - Mitigação: garantir que a porta seja liberada APENAS após confirmação de que o processo morreu; usar lock por porta.
- **Arquivos criados:**
  - `src/agent/process_utils.py` — funções `kill_process_tree(pid)`, `wait_for_process_death(pid, timeout)`, `is_port_free(port)`.
  - `src/agent/__init__.py` — exportar `BrowserSession` (já existe, verificar).
- **Arquivos modificados:**
  - `src/agent/browser_session.py` — refatorar `teardown()` para usar `process_utils`; adicionar registry de PIDs (`_active_pids: set[int]`).
  - `src/api/manager.py` — em `_execute()`, garantir que `teardown()` é chamado mesmo se `loop.run()` levantar exceção não-capturada.
- **Critérios de aceitação:**
  - [ ] Após 100 ciclos de setup/teardown, nenhum processo Chrome permanece na lista de processos do sistema.
  - [ ] `teardown()` é idempotente (chamar 2x não gera erro).
  - [ ] Logs indicam claramente quando um processo não pôde ser morto e qual foi o PID.
  - [ ] `psutil` é adicionado como dependência opcional (extra `dev` ou `systemd`).

---

### Task 4: Rate Limit Distribuído (Redis)
- **Complexidade:** Média
- **Riscos:**
  - Disponibilidade do Redis — se cair, a API deve continuar funcionando (graceful degradation).
  - Mitigação: fallback automático para in-memory com warning no log.
  - Latência extra de ~1ms por request (aceitável).
- **Arquivos criados:**
  - `src/api/rate_limiter.py` — classe abstrata `RateLimiter`, implementações `RedisRateLimiter` e `InMemoryRateLimiter`.
  - `src/api/config.py` — centralizar leitura de env vars (`REDIS_URL`, `RATE_LIMIT_WINDOW`, `RATE_LIMIT_MAX`).
- **Arquivos modificados:**
  - `src/api/server.py` — substituir `_check_rate_limit` e middleware por chamada ao `RateLimiter` injetado.
  - `tests/test_api.py` — testes de rate limit precisam agora mockar o backend Redis ou testar ambos.
- **Critérios de aceitação:**
  - [ ] Com Redis configurado, worker A e worker B compartilham o mesmo contador de rate limit.
  - [ ] Sem Redis, fallback in-memory funciona como antes.
  - [ ] A chave de rate limit usa token de API quando autenticado, IP quando anônimo.
  - [ ] Header `X-RateLimit-Remaining` é retornado nas respostas.

---

### Task 5: Testes Unitários do Agent Core
- **Complexidade:** Alta
- **Riscos:**
  - Mockar demais → testes não pegam regressões reais.
  - Mitigação: manter mocks apenas na fronteira (CDP, LLM, filesystem); usar dados reais de DOM/JSON nos testes.
  - Testes lentos → desincentivam rodar na CI.
  - Mitigação: garantir que nenhum teste unitário dispara Chrome, e que fixtures sejam baratas.
- **Arquivos criados:**
  - `tests/unit/agent/__init__.py`
  - `tests/unit/agent/conftest.py` — fixtures compartilhadas: `mock_browser`, `mock_cdp_conn`, `mock_llm`, `mock_run_state`.
  - `tests/unit/agent/test_browser_session.py` (~15 casos)
  - `tests/unit/agent/test_executor.py` (~20 casos)
  - `tests/unit/agent/test_loop.py` (~15 casos)
  - `tests/unit/agent/test_state.py` — testes para serialização/deserialização de `RunState`.
- **Arquivos modificados:**
  - Nenhum (exceto possíveis pequenas refatorações de visibilidade para facilitar testes, como extrair métodos privados).
- **Critérios de aceitação:**
  - [ ] Cobertura de `browser_session.py` ≥ 70% por linhas.
  - [ ] Cobertura de `executor.py` ≥ 60% por linhas.
  - [ ] Cobertura de `loop.py` ≥ 50% por linhas (parte de orquestração).
  - [ ] Todos os testes rodam em < 30s no CI.
  - [ ] `pytest tests/unit/agent/` passa sem Chrome instalado.

---

## 4. Matriz de Riscos Cross-Task

| Risco | Impacto | Probabilidade | Mitigação |
|-------|---------|---------------|-----------|
| Quebra de contrato de API para clientes existentes | Alto | Alta | Guardar `AUTH_DISABLED=true` como default por 1 sprint; deprecar explicitamente. |
| `psutil` não disponível em ambiente de deploy | Médio | Baixa | Tornar `psutil` opcional; fallback para `subprocess` + `os.kill` puro. |
| Redis não provisionado em ambiente de staging | Baixo | Média | Fallback automático in-memory; alerta de log diferenciado. |
| Shutdown lento (>30s) causa SIGKILL do K8s | Alto | Média | Timeouts agressivos em teardown; salvar estado assíncronamente sem bloquear. |
| Testes de loop.py são frágeis (muito mock) | Médio | Alta | Revisar mocks com padrão Arrange-Act-Assert claro; adicionar testes de integração leve em Fase B. |
| Race condition no port pool (RunManager) | Médio | Média | Substituir `_port_pool` (Queue) por lock asyncio + verificação `is_port_free()` antes de usar. |

---

## 5. Inventário de Dependências Python a Adicionar

| Pacote | Versão Mínima | Task | Motivo |
|--------|---------------|------|--------|
| `redis` | ≥5.0 | 4 | Cliente Redis async |
| `psutil` | ≥6.0 | 3 | Coleta de árvore de processos do Chrome |
| `httpx` | ≥0.27 | 1, 5 | Já usado em tests; garantir presente para testes de middleware |
| `pytest-asyncio` | ≥0.23 | 5 | Fixtures async nos testes unitários |

**Nota:** Verificar se `pytest-asyncio` já está no ambiente (usado em `tests/test_api.py`).

---

## 6. Checklist de Code Review por Task

Antes de marcar cada task como completa:

- [ ] `ruff check` passa sem novos erros.
- [ ] `mypy` passa nos arquivos novos/modificados.
- [ ] Todos os testes existentes continuam passando.
- [ ] Novos testes cobrem pelo menos os critérios de aceitação listados.
- [ ] Documentação da API (OpenAPI) refletiu mudanças (executar `curl /openapi.json` e verificar).
- [ ] Logs não exponham tokens, senhas ou cookies (revisar manualmente).

---

## 7. Ordem de Merge Recomendada

1. **Task 2 (Graceful Shutdown)** primeiro — cria infraestrutura de lifecycle usada por todas as outras.
2. **Task 3 (Zombie Prevention)** em seguida — beneficia imediatamente do lifecycle.
3. **Task 1 (Auth)** em paralelo — não tem dependência funcional.
4. **Task 4 (Rate Limit)** — pode usar token de auth como chave.
5. **Task 5 (Tests)** — último, valida estabilidade.

---

*Plano criado em: 2025-05-10*  
*Próxima fase sugerida: Fase B — Refatoração de Performance (batching, paralelismo de steps)*
