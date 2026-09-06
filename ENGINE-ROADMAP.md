# Ready AI — Engine-first roadmap (pivot browser automation)

> Congelamento: `PLAN_FASE_A.md`, `PLAN_FASE_B.md`, `PLAN_FASE_C.md`, `ROADMAP.md` e
> `ROADMAP-IMPROVEMENTS.md` ficam como histórico docs-centric e não recebem mais tasks.
> O motor CDP verificável é o produto; docs/test-runner vira consumidor de exemplo.

## Fase 1 — Harness E2E local (T-12)
- Resultado: testbed local sem credenciais cobrindo SPA, Shadow DOM, iframe cross-origin, pop-up, redirect, download, diálogo e desconexão CDP.
- Resultado: `pytest tests/e2e -q` verde em CI com Chrome isolado, timeout e limpeza de processo.
- Resultado: falha gera bundle sanitizado que permite reproduzir sem repetir a execução.

## Fase 2 — Núcleo preciso e seguro (T-11, T-6, T-7, T-8)
- Resultado: steps classificados read/write/irreversible com confirmação explícita e chave de idempotência por step (sem repetir efeito em retry).
- Resultado: contextos explícitos target/session/frame — pop-ups, abas e iframes OOPIF sem cruzar estado.
- Resultado: upload por allowlist, download com verificação de evento/nome/tamanho/MIME e diálogos com ação explícita.
- Resultado: SSO/MFA entra em checkpoint humano com retomada observável, sem exportar cookies nem automatizar desafio.

## Fase 3 — Replay determinístico zero-token (US7)
- Resultado: fluxo verificado compila para script CDP executado sem LLM; LLM só na autoria e na cura de drift.
- Resultado: fallback automático para modo agêntico quando o fingerprint diverge ou o passo falha.
- Resultado: métrica de custo antes/depois publicada no card (instrumentação US2).

## Fase 4 — Diagnóstico e precisão medida (T-9, US6)
- Resultado: toda falha terminal gera flight recorder sanitizado (passo, URL, DOM/AX, screenshot, console, rede, tempos) correlacionado ao run.
- Resultado: mutation harness publica healer scorecard (detection rate / false-positive rate por canal) no relatório HTML.
- Resultado: gate multi-causal (US5, já entregue) mantido: 1 canal sozinho gera `DRIFT_SUSPECTED`, nunca auto-heal silencioso.

## Fase 5 — SDK público (T-14, T-15)
- Resultado: `ReadyAI.run()` / `run_sync()` com streaming de eventos, cancelamento cooperativo e exceções públicas tipadas.
- Resultado: wheel com `py.typed`, tipos na superfície pública, SemVer e CI instalando o wheel em ambiente limpo nas versões Python suportadas.

## Trem de releases (atual: v0.3.0 → 5 releases, uma por fase)

| Release | Fase / Card Cortex | Conteúdo | Status |
|---------|--------------------|----------|--------|
| v0.2.0 | Fase 1 / `READY-AI-T-PH1-E2E-HARNESS` | Harness E2E local verde em CI | ✅ shipped |
| v0.3.0 | Fase 2 / `READY-AI-T-PH2-PRECISE-CORE` | Núcleo preciso e seguro | ✅ shipped |
| v0.4.0 | Fase 3 / `READY-AI-T-PH3-ZERO-TOKEN-REPLAY` | Replay determinístico zero-token | 🚧 next |
| v0.5.0 | Fase 4 / `READY-AI-T-PH4-DIAG-SCORE` | Flight recorder + healer scorecard | ⏳ |
| v0.6.0 | Fase 5 / `READY-AI-T-PH5-PUBLIC-SDK` | SDK público distribuível | ⏳ |

Cada release: bump em `pyproject.toml` + entrada no `CHANGELOG.md` + tag `vX.Y.Z`.
`READY-AI-T-PH0-DOCS-RELEASES` acompanha todas: README e `docs/ARCHITECTURE.md` atualizados por release.

## Pausado (fora do pivot)
- `US8` WebMCP ingestion (flag), `US9` export de Agent Skills — só após Fase 3.
- Pipeline docs (`test_runner`/`auto_healer`) — mantido como exemplo, sem novas features.
