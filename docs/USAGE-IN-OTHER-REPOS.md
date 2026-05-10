# Usando ready-ai no seu repositório de aplicação

Este guia explica como usar o `ready-ai` para documentar automaticamente a sua aplicação, **sem precisar do código do ready-ai no seu repo**.

---

## 🏗️ Arquitetura

```
┌─────────────────────────────────────────────────────────────────┐
│                      SEU REPO DE APLICAÇÃO                       │
│  ┌──────────────────┐    ┌──────────────────┐                   │
│  │ .github/workflows/│    │ .ready-ai.yaml   │                   │
│  │ docs-regression   │    │ (config dos      │                   │
│  │ docs-generation   │    │  flows)          │                   │
│  └──────────────────┘    └──────────────────┘                   │
│         │                                                     │
│         │ pip install ready-ai  (do PyPI)                     │
│         ▼                                                     │
│  ┌─────────────────────────────────────────┐                  │
│  │  ready-ai test|run|batch|export          │                  │
│  │  (baixado automaticamente no CI)         │                  │
│  └─────────────────────────────────────────┘                  │
└─────────────────────────────────────────────────────────────────┘
         │
         │ API calls (OpenAI + Chrome headless)
         ▼
   ┌──────────────────┐
   │  Sua aplicação   │  ← staging / production
   │  (documentada!)  │
   └──────────────────┘
```

---

## 📋 O que você precisa copiar para o seu repo

Copie **apenas estes arquivos** para o seu repositório de aplicação:

### 1. Workflows do GitHub Actions
```
.github/workflows/
├── docs-regression.yml      ← testa docs em todo PR
└── docs-generation.yml      ← gera docs em toda release/tag
```

### 2. Config file (opcional mas recomendado)
```
.ready-ai.yaml              ← define quais flows documentar
```

---

## 🔧 Setup passo-a-passo

### 1. Criar `.ready-ai.yaml` na raiz do seu repo

```yaml
# .ready-ai.yaml — configuração dos flows para documentar
app_version: auto          # ou hardcode
base_url: https://app.example.com
model: gpt-4o-mini

flows:
  - goal: "Document login flow"
    path: /login

  - goal: "Document dashboard"
    path: /dashboard

  - goal: "Document settings page"
    path: /settings
    title: "Settings & Preferences"
```

### 2. Copiar os workflows

#### `docs-regression.yml` (testa em PR)

```yaml
name: Documentation Regression

on:
  pull_request:
    paths: ["frontend/**", "src/**", "app/**", ".ready-ai.yaml"]

jobs:
  regression-test:
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write
    steps:
      - uses: actions/checkout@v4

      - name: Install ready-ai
        run: |
          pip install ready-ai
          sudo apt-get update && sudo apt-get install -y chromium-browser

      - name: Download baseline
        id: baseline
        run: |
          mkdir -p ./docs-baseline
          # Try latest artifact from docs-generation workflow
          LATEST_RUN=$(gh run list --workflow=docs-generation.yml --limit 1 --json databaseId --jq '.[0].databaseId')
          if [ -n "$LATEST_RUN" ]; then
            gh run download "$LATEST_RUN" --dir ./docs-artifact 2>/dev/null || true
            find ./docs-artifact -name "docs.md" -exec cp {} ./docs-baseline/docs.md \; 2>/dev/null || true
          fi
          [ -f "./docs-baseline/docs.md" ] && echo "found=true" >> $GITHUB_OUTPUT || echo "found=false" >> $GITHUB_OUTPUT

      - name: Run regression test
        if: steps.baseline.outputs.found == 'true'
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          STAGING_URL: ${{ vars.STAGING_URL }}
        run: |
          ready-ai test             --doc "./docs-baseline/docs.md"             --url "$STAGING_URL"             --threshold 0.85             --headless             --output "./regression-report"

      - name: Auto-regenerate on drift
        if: steps.test.outputs.status == 'DRIFT_DETECTED'
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: |
          ready-ai batch --config .ready-ai.yaml --output ./output
          for run_dir in ./output/*/; do
            run_id=$(basename "$run_dir")
            [ -f "$run_dir/docs.md" ] && ready-ai export --run-id "$run_id" --format markdown --output-dir "./docs-new/$run_id"
          done
          git add docs/
          git commit -m "docs: auto-regenerate [skip ci]" && git push || true
```

#### `docs-generation.yml` (gera em release)

```yaml
name: Generate Documentation

on:
  push:
    tags: ["v*"]
  workflow_dispatch:

jobs:
  generate-docs:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4

      - name: Install ready-ai
        run: |
          pip install ready-ai
          sudo apt-get update && sudo apt-get install -y chromium-browser

      - name: Run batch
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          BASE_URL: ${{ vars.BASE_URL }}
        run: |
          ready-ai batch             --config .ready-ai.yaml             --app-version "${GITHUB_REF#refs/tags/v}"             --output ./output

      - name: Export docs
        run: |
          for run_dir in ./output/*/; do
            run_id=$(basename "$run_dir")
            [ -f "$run_dir/docs.md" ] && ready-ai export --run-id "$run_id" --format docusaurus --output-dir "./docs-export/$run_id"
          done

      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: docs-${{ github.ref_name }}
          path: ./docs-export/
          retention-days: 30
```

### 3. Configurar Secrets e Variables no GitHub

No seu repositório de aplicação, vá em **Settings → Secrets and variables → Actions**:

#### Secrets (são encriptados)
| Nome | Valor |
|------|-------|
| `OPENAI_API_KEY` | sk-... (sua chave da OpenAI) |
| `PYPI_API_TOKEN` | (se você for publicar o ready-ai no PyPI) |

#### Variables (não são encriptados, visíveis)
| Nome | Valor | Exemplo |
|------|-------|---------|
| `STAGING_URL` | URL de staging | `https://staging.example.com` |
| `BASE_URL` | URL de produção | `https://app.example.com` |

---

## 🚀 Primeira rodada

1. **Crie a tag inicial** (dispara a primeira geração):
   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

2. **O workflow gera os docs** e publica como artifact.

3. **Agora todo PR** vai rodar regression test automaticamente!

---

## 🔄 Resumo do fluxo

| Evento | Workflow | Ação |
|--------|----------|------|
| PR aberto com mudança em `frontend/**` | `docs-regression.yml` | Testa baseline vs staging |
| Test PASSED | — | ✅ CI passa, comenta na PR |
| Test DRIFT | — | ⚠️ Regenera docs, comita na PR |
| Test BROKEN | — | ❌ CI falha, bloqueia merge |
| Tag `v*` pushada | `docs-generation.yml` | Gera docs da nova versão |

---

## 💡 Dica: Usar sem PyPI (instalar do GitHub)

Se não quiser publicar no PyPI, substitua `pip install ready-ai` por:

```bash
pip install git+https://github.com/pedro/ready-ai.git
```

Ou use a **composite action** (copie `.github/actions/ready-ai/action.yml` do repo do ready-ai).
