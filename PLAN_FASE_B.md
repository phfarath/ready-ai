# Plano Técnico — FASE B: Escalabilidade

## Contexto

A Fase A (MVP) entregou um pipeline funcional de geração de documentação via CDP + LLM, com API FastAPI, batch processing básico, checkpointing e tracing em memória. A Fase B foca em tornar o sistema escalável, resiliente e observável em produção.

Os 6 problemas críticos identificados são:

1. **Batch síncrono/sequencial** — `RunManager.start_batch()` itera `for flow in config.flows` com `await start_run()`; a request HTTP trava até o último flow ser iniciado.
2. **Sem circuit breaker para LLM** — `LLMClient._call_with_retry()` trata apenas `RateLimitError`; falhas contínuas (timeout, 5xx, API indisponível) explodem custo e latência.
3. **DOM é re-buscado a cada step sem cache** — `_get_page_context()` e `execute_step()` fazem 2–3 chamadas CDP por step. Em flows com 15+ steps, isso é centenas de round-trips desnecessários quando a página não mudou.
4. **WebSocket CDP não se reconecta automaticamente** — `CDPConnection._recv_loop()` loga e morre em `ConnectionClosed`; a única recuperação é o crash recovery pesado do `BrowserSession`, que reinicia o Chrome inteiro.
5. **Sem métricas Prometheus** — `observability.py` tem `Metrics` em memória e `Span` estruturado, mas nenhum endpoint `/metrics` Prometheus para dashboards (Grafana) e alerting.
6. **Port pool pode esgotar permanentemente** — `asyncio.Queue` simples sem TTL, health-check de portas, nem timeout de aquisição. Portas podem leakar se exceções ocorrerem entre `_acquire_port` e `_release_port`.

---

## Sumário de Tasks (ordem de dependência)

| # | Task | Complexidade | Dependências |
|---|------|--------------|--------------|
| 1 | Port pool resiliente com TTL e health check | Média | — |
| 2 | Batch assíncrono com semáforo de concorrência | Média | 1 |
| 3 | Cache de estado DOM/CDP por step | Alta | — |
| 4 | Reconexão automática do WebSocket CDP | Alta | 3 |
| 5 | Circuit breaker no LLM client | Média | — |
| 6 | Export Prometheus + endpoint /metrics | Baixa | — |

---

## Task 1: Port Pool Resiliente com TTL e Health Check

**Arquivos:**
- **Modificar:** `src/api/manager.py`
- **Criar:** `src/api/port_pool.py`

**Arquitetura:**
Substituir a `asyncio.Queue` por um `PortPool` dedicado com:
- `acquire(timeout=30.0)`: bloqueia com timeout configurável. Se a fila está vazia, tenta uma porta recém-liberada que passou por health-check.
- `release(port)`: devolve à fila apenas se `health_check(port)` passar.
- `_health_check(port)`: tenta conectar em `localhost:<port>` via socket TCP com timeout curto (1s). Se a porta não responder Chrome DevTools, marca como suspeita e tenta limpar processos órfãos (via `psutil` ou `subprocess` no Windows).
- `_recovery_sweep()`: task background periódica (a cada 60s) que escaneia o range de portas, verifica quais estão realmente em uso pelo Chrome, e recoloca na fila as que estão livres mas sumiram do pool (leak recovery).

**Riscos & Mitigação:**
- **Risco:** No Windows, listar processos por porta requer `netstat`/`Get-NetTCPConnection` ou `psutil`.  
  **Mitigação:** Usar `psutil` (cross-platform). Adicionar `psutil>=5.9` em `pyproject.toml`.
- **Risco:** Health-check TCP pode confundir outro serviço na mesma porta.  
  **Mitigação:** Fazer `GET /json/version` na porta para validar CDP, não apenas handshake TCP.

**Complexidade:** Média (~1.5 dias)

---

## Task 2: Batch Assíncrono com Semáforo de Concorrência

**Arquivos:**
- **Modificar:** `src/api/manager.py`
- **Modificar:** `src/api/server.py` (adicionar query param `concurrency`)
- **Criar:** `src/api/batch_processor.py`

**Arquitetura:**
Hoje `start_batch` é um `for … await` síncrono. O novo `BatchProcessor` deve:
1. Receber o `BatchConfig` e um `batch_id`.
2. Criar um semáforo `asyncio.Semaphore(max_concurrent)` para limitar o número de flows simultâneos (default = número de CPUs * 2, ou 5).
3. Para cada `flow`, criar uma `asyncio.Task` que chama `RunManager.start_run()` dentro do semáforo.
4. Retornar imediatamente um `BatchRunResponse` com `status=“ACCEPTED”`, sem esperar os tasks terminarem.
5. Manter o `_batches` dict atualizado com progresso via callbacks nos tasks.

**Interface:**
```python
class BatchProcessor:
    async def submit(self, config: BatchConfig, batch_id: str, max_concurrent: int = 5) -> BatchRunResponse: ...
    def get_status(self, batch_id: str) -> BatchStatusResponse: ...
```

**Mudanças em `RunManager`:**
- Extrair a lógica de port pool para a Task 1.
- Garantir que `start_run` seja thread-safe para chamadas concorrentes (o port pool já lida com isso).

**Mudanças em `server.py`:**
- Adicionar `?concurrency=5` no endpoint `POST /batch`.
- Garantir que o middleware de rate-limit não bloqueie batch (ou tenha limit separado mais alto).

**Riscos & Mitigação:**
- **Risco:** Chrome consome ~100–300 MB por instância; 50 flows paralelos podem OOM-kill.  
  **Mitigação:** Default `max_concurrent=3` e documentar limites de memória por worker. Adicionar validação no `BatchConfig` se `len(flows) > 20` exige confirmação.
- **Risco:** Observability em memória (`RunContext`) pode misturar métricas entre runs concorrentes se `contextvars` vazarem.  
  **Mitigação:** Garantir que `init_run_context()` seja chamado dentro do task de cada run (já é assim, mas revisar).

**Complexidade:** Média (~1.5 dias)

---

## Task 3: Cache de Estado DOM/CDP por Step

**Arquivos:**
- **Modificar:** `src/agent/executor.py`
- **Modificar:** `src/agent/loop.py`
- **Criar:** `src/agent/dom_cache.py`

**Arquitetura:**
O executor precisa de `dom_html`, `interactive_elements` e `current_url` a cada tentativa de um step. Se a página não mudou (ex: retry do mesmo step após falha de selector), esses dados são idênticos.

`DOMCache`:
- Chave: `hash(url + window.location.href + document.title + dom_fingerprint)`
- TTL: curto (5s), pois a página pode mudar entre retries.
- `async def get_dom_state(page, runtime) -> DOMState`: se hit, retorna cache; se miss, busca e armazena.

**Pontos de uso:**
1. `AgenticLoop._get_page_context()` → usar `DOMCache.get()`.
2. `AgenticLoop._execute_steps()` → buscar `dom_html` / `elements` uma única vez por step, passar para `executor.execute_step()`.
3. `executor.execute_step()` → no loop de retry (linhas 86–201), reaproveitar o `dom_html` e `interactive_elements` do primeiro attempt. Só refazer a busca se `action_type` foi `navigate`, `click` ou o DOM fingerprint mudar.

**Algoritmo no executor:**
```python
for attempt in range(1, MAX_RETRIES + 1):
    if attempt == 1 or dom_changed(last_action):
        dom_html, elements, url = await dom_cache.get(page, runtime)
    action = await _get_action(step, dom_html, elements, ...)
    ...
```

**Riscos & Mitigação:**
- **Risco:** Cache stale se SPA faz transição suave (React/Vue router) e o hash não captura mudança.  
  **Mitigação:** Incluir `window.location.href + document.body.innerHTML.length + Date.now//1000` no hash. Em caso de dúvida, invalidar cache após qualquer ação de `navigate`, `click`, `scroll_to`.
- **Risco:** Consumo de memória se DOMs enormes ficam em cache.  
  **Mitigação:** Cache com `maxsize=10` (LRU) e truncar HTML para o mesmo `max_length` usado no prompt (4000 chars).

**Complexidade:** Alta (~2.5 dias) — impacta o core loop crítico.

---

## Task 4: Reconexão Automática do WebSocket CDP

**Arquivos:**
- **Modificar:** `src/cdp/connection.py`
- **Modificar:** `src/cdp/browser.py`
- **Criar:** `src/cdp/reconnect.py`

**Arquitetura:**
Atualmente `CDPConnection` é stateful (`_ws`, `_session_id`, `_pending`). Quando o WS cai, todo o pipeline falha. Precisamos de reconexão transparente:

**Estratégia:**
1. **Decorator/Wrapper de envio:** `CDPConnection.send()` deve detectar `websockets.exceptions.ConnectionClosed` e entrar em modo de reconexão.
2. **ReconnectionPolicy:**
   - Backoff exponencial: 0.5s, 1s, 2s, 4s, 8s (max 30s).
   - Max tentativas: 5.
   - Durante reconexão, rejeitar novos `send()` com exceção clara.
3. **Re-hidratação de sessão:**
   - Ao reconectar, chamar `Target.getTargets` → reanexar ao page target.
   - Re-ativar domains (`Page.enable`, `DOM.enable`, `Runtime.enable`).
   - Re-injetar cursor script (`register_cursor_script`).
4. **Re-hidratação de comandos pendentes:**
   - `_pending` futuros que estavam esperando resposta antes da queda serão cancelados com `CDPReconnectError`.
   - Callers (ex: `page.navigate()`) devem tratar e, se apropriado, retryar no nível superior.

**Mudanças em `browser.py`:**
- `get_ws_url()` deve ser robusto a Chrome ainda inicializando (retry 503/ConnectionRefused).

**Mudanças em `browser_session.py`:**
- `BrowserSession.setup()` aceita retry interno de conexão, mas delega a política ao `CDPConnection`.

**Riscos & Mitigação:**
- **Risco:** Reconectar pode anexar a um page target diferente (nova aba).  
  **Mitigação:** Guardar `targetId` original e tentar reanexar ao mesmo; se não existir, buscar o page target mais recente.
- **Risco:** Pending futures podem ficar órfãos e memory-leak.  
  **Mitigação:** No `finally` do `_recv_loop`, iterar `_pending` e `set_exception(CDPConnectionLost)` em todos.
- **Risco:** Timeout de 30s no `send()` pode não ser suficiente com backoff.  
  **Mitigação:** Separar timeout de *comando* (30s) de timeout de *reconexão* ( backoff + 5s ).

**Complexidade:** Alta (~2.5 dias) — networking + state machine.

---

## Task 5: Circuit Breaker no LLM Client

**Arquivos:**
- **Modificar:** `src/llm/client.py`
- **Criar:** `src/llm/circuit_breaker.py`

**Arquitetura:**
Circuit breaker de 3 estados: `CLOSED`, `OPEN`, `HALF_OPEN`.
- **Falha considerada:** qualquer exceção exceto `RateLimitError` (que já tem backoff próprio). Inclui `TimeoutError`, `APIConnectionError`, `AuthenticationError`, `InternalServerError`.
- **Threshold:** 5 falhas em 60 segundos → `OPEN`.
- **Cooldown:** 30s em `OPEN`, depois `HALF_OPEN` permite 1 probe.
- **Sucesso no probe:** volta a `CLOSED`.
- **Falha no probe:** volta a `OPEN` com cooldown dobrado (max 5 min).

**Mudanças em `LLMClient`:**
- Adicionar `CircuitBreaker` como atributo de classe (compartilhado entre instâncias do mesmo modelo, ou global).
- No `_call_with_retry`, envolver a chamada real no breaker.
- Em `OPEN`, levantar `LLMCircuitOpenError` imediatamente sem tocar na API (economia de $).

**Observabilidade:**
- Emitir métricas: `llm.circuit_state` (gauge 0=closed,1=open,2=half_open), `llm.circuit_failures_total` (counter), `llm.circuit_opens_total` (counter).

**Riscos & Mitigação:**
- **Risco:** Circuit breaker global pode derrubar calls de runs saudáveis por causa de um run com prompt problemático.  
  **Mitigação:** Usar breaker *por instância* de `LLMClient` (cada run tem seu próprio). Ou, se global, separar por `model` + `role`.
- **Risco:** Rate limit não é falha do sistema, mas do usuário. Não deve contar para o breaker.  
  **Mitigação:** Excluir `RateLimitError` do contador de falhas (já é assim, mas revisar para outras exceções do litellm).

**Complexidade:** Média (~1.5 dias)

---

## Task 6: Export Prometheus + Endpoint /metrics

**Arquivos:**
- **Modificar:** `src/observability.py`
- **Modificar:** `src/api/server.py`
- **Modificar:** `pyproject.toml`
- **Criar:** `src/observability/prometheus_exporter.py`

**Arquitetura:**
1. **Adicionar dependência:** `prometheus-client>=0.20` em `pyproject.toml`.
2. **Criar `PrometheusExporter`:**
   - Mapear métricas internas do `Metrics` para tipos Prometheus:
     - `llm_calls_total` (counter, label: role)
     - `llm_latency_seconds` (histogram, label: role, buckets: [.01, .05, .1, .25, .5, 1, 2.5, 5, 10])
     - `llm_prompt_tokens_total`, `llm_completion_tokens_total` (counter, label: role)
     - `llm_cost_usd_total` (counter, label: role)
     - `steps_executed_total`, `steps_succeeded_total`, `steps_failed_total` (counter)
     - `step_latency_seconds` (histogram)
     - `recovery_events_total` (counter, label: type)
     - `runs_active` (gauge)
     - `browser_ports_available` (gauge) — alimentado pelo port pool
3. **Hook no `Metrics`:**
   - Ao invés de `get_metrics()` retornar apenas `Metrics`, expor um método `Metrics.flush_to_prometheus()` chamado periodicamente ou no `run_summary()`.
4. **Endpoint `/metrics`:**
   - Em `server.py`, adicionar `from prometheus_client import make_asgi_app` e montar em `/metrics`.

**Riscos & Mitigação:**
- **Risco:** `prometheus_client` default expõe *todas* as métricas do processo (GC, memória). Pode ser muito ruído.  
  **Mitigação:** Usar `generate_latest()` com `REGISTRY` controlado, não o default.
- **Risco:** Métricas acumulam em memória se o processo é long-running (API server).  
  **Mitigação:** Counters/prometheus-client já são agregados; histograms têm buckets fixos. OK.

**Complexidade:** Baixa (~0.5 dias)

---

## Ordem de Implementação Recomendada

```
Semana 1
├── Task 1 (Port Pool)         → base para concorrência segura
├── Task 6 (Prometheus)      → paralelizável, baixo risco, dá visibilidade para o resto

Semana 2
├── Task 5 (Circuit Breaker) → paralelizável com Task 1
├── Task 2 (Batch Assínc.)   → depende do Port Pool pronto

Semana 3
├── Task 3 (DOM Cache)       → alto risco, requer testes extensivos
├── Task 4 (CDP Reconnect)   → alto risco, compartilha state machine com cache

Semana 4
├── Integração & Testes
├── Load test do batch (ex: 10 flows simultâneos)
├── Ajustes finais
```

---

## Testes a Adicionar

1. **`tests/test_port_pool.py`**: 
   - `test_acquire_timeout`, `test_release_unhealthy_port`, `test_leak_recovery`, `test_concurrent_acquire`.
2. **`tests/test_batch_processor.py`**:
   - `test_batch_parallel_limit`, `test_batch_returns_accepted_immediately`, `test_batch_status_tracks_progress`.
3. **`tests/test_dom_cache.py`**:
   - `test_cache_hit_same_url`, `test_cache_invalidated_on_navigate`, `test_cache_lr eviction`.
4. **`tests/test_cdp_reconnect.py`**:
   - `test_reconnect_after_ws_drop`, `test_pending_commands_get_error_on_disconnect`, `test_exponential_backoff`.
5. **`tests/test_llm_circuit_breaker.py`**:
   - `test_opens_after_threshold`, `test_half_open_probe`, `test_resets_on_success`, `test_rate_limit_excluded`.
6. **`tests/test_prometheus.py`**:
   - `test_metrics_endpoint_exists`, `test_llm_counters_incremented`, `test_browser_ports_gauge`.

---

## Dependências Python Adicionais

```toml
[project.optional-dependencies]
prod = [
    "prometheus-client>=0.20.0",
    "psutil>=5.9.0",
]
```

> Nota: `prometheus-client` e `psutil` devem ser incluídos na imagem Docker / ambiente de produção, não obrigatoriamente no `dev`.

---

## Decisões Arquiteturais

| Decisão | Justificativa |
|---------|----------------|
| Port pool como classe separada (`src/api/port_pool.py`) | Facilita testes unitários e permite reuso caso o API manager mude. |
| CDP reconnect dentro de `CDPConnection` (não no BrowserSession) | Transparência para domínios superiores (`PageDomain`, `RuntimeDomain`). |
| Circuit breaker por `LLMClient` instância (default global opt-in) | Evita que um modelo ruim (ex: GPT-4 indisponível) quebre chamadas de outro modelo (ex: Claude). |
| DOM cache com invalidação por action type | Simples e efetivo; não requer diffing de DOM que seria CPU-intensive. |
| Semáforo ao invés de queue de workers para batch | Menos código, aproveita o event loop asyncio existente. |

---

## Checklist antes de iniciar implementação

- [ ] Criar branch `fase-b/escalabilidade`
- [ ] Adicionar `prometheus-client` e `psutil` ao `pyproject.toml`
- [ ] Configurar CI para rodar novos testes com `pytest-xdist` (paralelismo)
- [ ] Definir baseline de performance (tempo para 1 flow X, 5 flows sequenciais) para medir ganho pós-Fase B
