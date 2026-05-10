# 📋 Checklist Final — Publicar ready-ai no PyPI + Usar em Outro Repo

Este documento tem **EXATAMENTE** o que você precisa fazer, na ordem certa.

---

## ✅ PARTE 1: Publicar no PyPI (Sua conta)

### Passo 1.1 — Criar conta no PyPI

1. Vá em https://pypi.org/account/register/
2. Confirme o email (check spam)
3. Habilite **2FA** (obrigatório agora)

### Passo 1.2 — Configurar Trusted Publishing (OIDC)

1. Logado no PyPI, vá em: **Your Projects → (ainda não tem projeto) → Publishing**
2. Ou direto: https://pypi.org/manage/account/publishing/
3. Clique em **"Add a new pending publisher"**
4. Preencha:
   - **PyPI Project Name:** `ready-ai`
   - **Repository:** `pedro/ready-ai` (troque "pedro" pelo seu username)
   - **Workflow:** `publish.yml`
   - **Environment name:** `pypi`
5. Clique **Add**

> ✅ Isso autoriza o GitHub Actions a publicar SEM precisar de token manual.

### Passo 1.3 — Commit e push da versão

```bash
cd /c/Dev/ready-ai
git add -A
git commit -m "feat: complete Phase 3, add export CLI, new API endpoints, workflows"

# Push pro seu repositório
# Se seu remote não estiver configurado:
# git remote add origin https://github.com/pedro/ready-ai.git
# git push -u origin main

git push
```

### Passo 1.4 — Criar tag e disparar publicação

```bash
git tag v0.1.0
git push origin v0.1.0
```

Isso dispara automaticamente `.github/workflows/publish.yml` e publica no PyPI!

### Passo 1.5 — Verificar no PyPI

1. Vá em https://pypi.org/project/ready-ai/
2. Deve aparecer a versão 0.1.0 em ~2 minutos

---

## ✅ PARTE 2: Usar em Outro Repositório (Sua Aplicação)

### Passo 2.1 — Criar `.ready-ai.yaml` na raiz do repo da sua app

Copie este arquivo (ajuste os paths):

```yaml
# .ready-ai.yaml
app_version: auto
base_url: https://app.example.com
model: gpt-4o-mini
headless: true

flows:
  - goal: "Document login and authentication flow"
    path: /login

  - goal: "Document dashboard overview"
    path: /dashboard

  - goal: "Document user settings"
    path: /settings
    title: "Settings & Preferences"
```

> Ajuste `base_url` e os `flows` para as páginas REAIS da sua aplicação.

### Passo 2.2 — Configurar Secrets e Variables no GitHub

No repo da sua aplicação, vá em: **Settings → Secrets and variables → Actions**

#### ➡️ Secrets (Aba "Secrets")

| Nome | Valor | Onde pega |
|------|-------|-----------|
| `OPENAI_API_KEY` | `sk-...` | https://platform.openai.com/api-keys |

Clique em **"New repository secret"** para cada um.

#### ➡️ Variables (Aba "Variables")

| Nome | Valor | Exemplo |
|------|-------|---------|
| `STAGING_URL` | URL de staging | `https://staging.minhaapp.com` |
| `BASE_URL` | URL de produção | `https://app.minhaapp.com` |

Clique em **"New repository variable"** para cada um.

### Passo 2.3 — Copiar os workflows

Crie os arquivos no repo da sua aplicação:

#### `.github/workflows/docs-generation.yml`

Copie de: [ready-ai/.github/workflows/docs-generation.yml](https://github.com/pedro/ready-ai/blob/main/.github/workflows/docs-generation.yml)

Ou use este resumo:

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

      - name: Generate docs
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: |
          ready-ai batch --config .ready-ai.yaml --output ./output

      - name: Export to Markdown
        run: |
          for dir in ./output/*/; do
            run_id=$(basename "$dir")
            [ -f "$dir/docs.md" ] && ready-ai export --run-id "$run_id" --format markdown --output-dir "./docs-export/$run_id"
          done

      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: docs-${{ github.ref_name }}
          path: ./docs-export/
          retention-days: 30
```

#### `.github/workflows/docs-regression.yml`

Copie de: [ready-ai/.github/workflows/docs-regression.yml](https://github.com/pedro/ready-ai/blob/main/.github/workflows/docs-regression.yml)

Resumo:
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
          ready-ai download-latest --output ./docs-baseline  # se implementarmos

      - name: Run regression test
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: |
          ready-ai test --doc "./docs-baseline/docs.md" --url "$STAGING_URL" --headless

      - name: Comment PR
        uses: actions/github-script@v7
        # ... (comentário automático na PR)
```

> ➡️ **Copie os arquivos completos do ready-ai** (eles estão prontos e testados).

### Passo 2.4 — Commit e push

```bash
cd /caminho/para/repo-da-sua-aplicacao
git add .
git commit -m "ci: add ready-ai documentation pipeline"
git push
```

### Passo 2.5 — Criar primeira release (gera baseline)

```bash
git tag v0.1.0
git push origin v0.1.0
```

Isso dispara `docs-generation.yml` e cria o primeiro artifact baseline. Agora o regression vai funcionar!

---

## ✅ VERIFICAÇÃO

### No repo do ready-ai:
- [ ] `git push` feito
- [ ] Tag `v0.1.0` criada e pushada
- [ ] Workflow `publish.yml` rodou com sucesso (check Actions tab)
- [ ] Pacote aparece em https://pypi.org/project/ready-ai/

### No repo da aplicação:
- [ ] `.ready-ai.yaml` configurado com seus flows
- [ ] Secrets `OPENAI_API_KEY` configurado
- [ ] Variables `STAGING_URL` e `BASE_URL` configurados
- [ ] Workflows copiados para `.github/workflows/`
- [ ] Primeira tag `v0.1.0` criada (gera baseline)
- [ ] Abrir um PR qualquer (workflow de regression roda automaticamente)
- [ ] PR recebe comment com resultado

---

## 📄 Arquivos criados/modificados nesta sessão

No repo **ready-ai**:
1. `main.py` — `export` CLI adicionado
2. `src/api/server.py` — 6 novos endpoints (GET /runs, GET /doc-sets, etc.)
3. `src/api/models.py` — Novos modelos Pydantic
4. `src/history.py` — Módulo de tracking histórico
5. `src/docs/export.py` — Export para 5 formatos
6. `.github/workflows/docs-regression.yml` — Regression pipeline completo
7. `.github/workflows/docs-generation.yml` — Release pipeline completo
8. `.github/workflows/publish.yml` — Publicação PyPI
9. `docs/USAGE-IN-OTHER-REPOS.md` — Guia completo de uso

No repo da **sua aplicação** você copiará:
- `.ready-ai.yaml`
- `.github/workflows/docs-regression.yml`
- `.github/workflows/docs-generation.yml`
