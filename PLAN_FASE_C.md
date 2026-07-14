# Plano Técnico — FASE C: Qualidade de Arquitetura

## Contexto

A Fase C foca em refatoração estrutural e abstrações de alto nível para reduzir dívida técnica, aumentar testabilidade e eliminar lock-ins. A ordem das tasks é incremental: cada task pode ser desenvolvida independentemente, mas têm uma sequência lógica recomendada.

## Tasks resumidas

| # | Task | Complexidade | Dependências |
|---|------|--------------|--------------|
| 1 | Refatorar loop.py em pipeline handlers | Alta | — |
| 2 | Validação de seletores antes de execução | Média | — |
| 3 | Pluggable LLM Provider (interface base) | Alta | — |
| 4 | Auto-recovery WebSocket CDP | Média | — |
| 5 | Documentação de troubleshooting | Baixa | — |

---

## Task 1: Refatorar AgenticLoop God Object

**Arquivos:**
- Modificar: `src/agent/loop.py` (de 592 → ~200 linhas como orquestrador)
- Criar:
  - `src/agent/handlers/setup_handler.py` — Chrome lifecycle, auth, navigate inicial
  - `src/agent/handlers/planning_handler.py` — gera plano e resume state
  - `src/agent/handlers/execution_pipeline.py` — executa steps
  - `src/agent/handlers/critique_handler.py` — revisa e re-executa
  - `src/agent/handlers/checkpoint_manager.py` — salva/resume RunState
  - `src/agent/handlers/renderer_handler.py` — chama DocRenderer e exporta

**Protocolo de interface (padrão comum):**
```python
class PipelineHandler(ABC):
    async def handle(self, ctx: PipelineContext) -> PipelineContext:
        ...
    @property
    def name(self) -> str: ...
```

**PipelineContext (dataclass imutável):**
- run_id, goal, url, llm, annotation_llm, session, doc, step_results, state

**Arquitetura:**
```
AgenticLoop.run():
  ctx = PipelineContext(...)
  pipeline = [
      SetupHandler(),
      PlanningHandler(),
      ExecutionPipeline(),
      CritiqueHandler(),
      RendererHandler(),
  ]
  for handler in pipeline:
      ctx = await handler.handle(ctx)
      checkpoint.save(ctx)
```

**Critério de aceitação:**
- `loop.py` < 250 linhas
- Nenhum handler com > 150 linhas
- Testes unitários para cada handler isoladamente (FakeSession, FakeLLM)
- Pipeline roda mesmo se 1 handler falhar (graceful degradation)

**Riscos:**
- Quebrar checkpoint resume existente → mitigar com testes de compatibilidade JSON
- Performance piora com muitos objetos → mitigar com dataclasses slots

---

## Task 2: Validação de Seletores no DOM

**Arquivos:**
- Criar: `src/agent/selector_validator.py`
- Modificar: `src/agent/executor.py`

**Problema:** Executor tenta `click("#btn-submit")` sem verificar se o seletor existe no DOM. Se não existir, desperdiça 1 LLM call de retry.

**Solução:**
```python
async def validate_selector(page, selector: str) -> tuple[bool, str | None]:
    """Retorna (existe?, alternativa sugerida ou None)"""
    found = await page.find_element(selector)
    if found:
        return True, None
    # Se não encontrou, pede ao LLM para gerar alternativa baseada no DOM
    alternatives = await page.find_similar_elements(selector)
    return False, alternatives[0] if alternatives else None
```

**No executor:**
```python
valid, alternative = await validate_selector(page, selector)
if not valid:
    if alternative:
        logger.warning(f"Selector {selector} not found, trying {alternative}")
        selector = alternative
    else:
        result.retry_needed = True
        result.failure_reason = f"Selector '{selector}' not found in DOM"
        return result
```

**Critério de aceitação:**
- Retry rate de steps cai em 20%+ em testes e2e
- Nenhum crash por seletor inválido (todos convertidos para retry)

---

## Task 3: Pluggable LLM Provider (eliminar lock-in)

**Arquivos:**
- Criar:
  - `src/llm/provider.py` (interface ABC)
  - `src/llm/providers/openai_provider.py`
  - `src/llm/providers/anthropic_provider.py`
  - `src/llm/providers/ollama_provider.py`
- Modificar:
  - `src/llm/client.py` (vira factory + router)

**Interface base:**
```python
class LLMProvider(ABC):
    @property
    def name(self) -> str: ...
    
    @abstractmethod
    async def complete(self, messages: list[dict], *, json_mode: bool = False, max_tokens: int = 4096) -> str: ...
    
    @abstractmethod
    async def vision(self, messages: list[dict], image_base64: str, *, max_tokens: int = 4096) -> str: ...
    
    @property
    def supports_vision(self) -> bool:
        return True
    
    @property
    def supports_json_mode(self) -> bool:
        return True
```

**Configuração via env:**
```bash
READY_AI_LLM_PROVIDER=ollama
READY_AI_LLM_MODEL=llama3.2
READY_AI_LLM_BASE_URL=http://localhost:11434
```

**Critério de aceitação:**
- Troca de provider via 1 variável de ambiente
- Todos os 3 providers passam em suite de testes de integração (com mocks)
- LLMClient mantém retry/circuit breaker (Fase B) intactos

---

## Task 4: Auto-recovery WebSocket CDP

**Arquivos:**
- Modificar: `src/cdp/connection.py`
- Criar: `src/cdp/connection.py` — `_reconnect()` + `_rehydrate()`

**Problema:** Hoje `ConnectionClosed` no `_recv_loop` mata tudo. O único recovery é `BrowserSession` reiniciar Chrome inteiro.

**Solução (em resumo, detalhes em Fase B Task 4):**
```python
async def _recv_loop(self):
    while not self.closed:
        try:
            message = await self.ws.receive()
            ...
        except websockets.ConnectionClosed:
            if not self._should_reconnect:
                break
            await self._reconnect()
            continue
```

**Critério de aceitação:**
- Desconexão temporária (< 5s) se recupera sem perder steps
- Desconexão > 30s → falha o step com retry (não reinicia browser)

---

## Task 5: Documentação de Troubleshooting

**Arquivo:** `docs/TROUBLESHOOTING.md`

**Seções:**
1. Chrome não encontrado (Linux/Windows/MacOS)
2. Chrome crasha no Docker
3. LLM API timeout — o que fazer?
4. Rate limit — esperar ou mudar de provider?
5. Porta 9222 já ocupada
6. SPA não carrega (wait_for_navigation_settled)
7. Screenshots pretos (headless mode)
8. Como debugar com `SPAN_DEBUG=true`

**Critério de aceitação:**
- 90%+ das dúvidas de suporte responderem por link para esse doc
- Dashboard Grafana para métricas (se Fase B entregou)

---

## Ordem recomendada de execução

1. **Task 3** (pluggable LLM) — reduz lock-in e facilita testes
2. **Task 2** (validador de seletores) — quick win, reduz custo LLM
3. **Task 4** (reconnect CDP) — depende de DOM cache da Fase B
4. **Task 1** (refatorar loop.py) — faça por último, é o mais disruptivo
5. **Task 5** (docs) — paralelizável em qualquer momento

## Dependências das Fases

- Fase C Task 4 depende da **Fase B Task 3** (DOM cache) para reconexão sem re-fetch total
- Fase C Task 1 (refatoração) beneficia-se da **Fase B Task 5** (Prometheus) para já ter métricas antes de mover código
- Fase C Task 3 (pluggable LLM) beneficia-se do **circuit breaker da Fase B Task 5**
