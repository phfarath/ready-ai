"""Tests for the documentation parser (src/docs/parser.py)."""

import pytest
from pathlib import Path
from src.docs.parser import parse_doc, extract_goal


# ─── Fixtures ───────────────────────────────────────────────────────────


SAMPLE_DOC_EN = """\
# Create a new project

> Automatically generated documentation on 2025-06-15 14:30

---

## Index

1. [Click the New Project button](#step-1)
2. [Type the project name](#step-2)
3. [Click Submit](#step-3)

---

## Step 1: Click the New Project button

![Step 1](screenshots/step_01.png)

Navigate to the dashboard and locate the "New Project" button in the top-right corner.

<details>
<summary>Technical details</summary>

**Action executed:** click on button#new-project

</details>

---

## Step 2: Type the project name

![Step 2](screenshots/step_02.png)

Enter your desired project name in the input field.

<details>
<summary>Technical details</summary>

**Action executed:** type "My Project" in input#project-name

</details>

---

## Step 3: Click Submit

![Step 3](screenshots/step_03.png)

Click the Submit button to create your project.

<details>
<summary>Technical details</summary>

**Action executed:** click on button.submit-btn

</details>

---
"""


SAMPLE_DOC_PT = """\
# Criar um novo projeto

> Documentação gerada automaticamente em 2025-06-15 14:30

---

## Índice

1. [Clicar no botão Novo Projeto](#passo-1)
2. [Digitar o nome do projeto](#passo-2)

---

## Passo 1: Clicar no botão Novo Projeto

![Passo 1](screenshots/step_01.png)

Navegue até o dashboard e localize o botão "Novo Projeto".

<details>
<summary>Detalhes técnicos</summary>

**Ação executada:** click on button#new-project

</details>

---

## Passo 2: Digitar o nome do projeto

![Passo 2](screenshots/step_02.png)

Digite o nome do projeto no campo de texto.

<details>
<summary>Detalhes técnicos</summary>

**Ação executada:** type "Meu Projeto" in input#project-name

</details>

---
"""


SAMPLE_DOC_MINIMAL = """\
# Simple flow

## Step 1: Open page

![Step 1](screenshots/step_01.png)

Open the application.

---
"""


# ─── Tests ──────────────────────────────────────────────────────────────


def test_parse_english_doc(tmp_path: Path):
    doc_file = tmp_path / "docs.md"
    doc_file.write_text(SAMPLE_DOC_EN, encoding="utf-8")

    steps = parse_doc(str(doc_file))

    assert len(steps) == 3

    # Step 1
    assert steps[0].number == 1
    assert steps[0].title == "Click the New Project button"
    assert steps[0].screenshot_path == "screenshots/step_01.png"
    assert "click on button#new-project" in steps[0].action_description

    # Step 2
    assert steps[1].number == 2
    assert steps[1].title == "Type the project name"
    assert 'type "My Project"' in steps[1].action_description

    # Step 3
    assert steps[2].number == 3
    assert steps[2].title == "Click Submit"


def test_parse_portuguese_doc(tmp_path: Path):
    doc_file = tmp_path / "docs.md"
    doc_file.write_text(SAMPLE_DOC_PT, encoding="utf-8")

    steps = parse_doc(str(doc_file))

    assert len(steps) == 2
    assert steps[0].number == 1
    assert steps[0].title == "Clicar no botão Novo Projeto"
    assert "click on button#new-project" in steps[0].action_description

    assert steps[1].number == 2
    assert steps[1].title == "Digitar o nome do projeto"


def test_parse_minimal_doc(tmp_path: Path):
    doc_file = tmp_path / "docs.md"
    doc_file.write_text(SAMPLE_DOC_MINIMAL, encoding="utf-8")

    steps = parse_doc(str(doc_file))

    assert len(steps) == 1
    assert steps[0].number == 1
    assert steps[0].title == "Open page"
    assert steps[0].action_description == ""  # no <details> block


def test_extract_goal(tmp_path: Path):
    doc_file = tmp_path / "docs.md"
    doc_file.write_text(SAMPLE_DOC_EN, encoding="utf-8")

    goal = extract_goal(str(doc_file))
    assert goal == "Create a new project"


def test_extract_goal_portuguese(tmp_path: Path):
    doc_file = tmp_path / "docs.md"
    doc_file.write_text(SAMPLE_DOC_PT, encoding="utf-8")

    goal = extract_goal(str(doc_file))
    assert goal == "Criar um novo projeto"


def test_parse_nonexistent_file():
    with pytest.raises(FileNotFoundError):
        parse_doc("/nonexistent/docs.md")


def test_parse_empty_doc(tmp_path: Path):
    doc_file = tmp_path / "docs.md"
    doc_file.write_text("# No steps here\n\nJust some text.", encoding="utf-8")

    with pytest.raises(ValueError, match="No steps found"):
        parse_doc(str(doc_file))


def test_annotations_extracted(tmp_path: Path):
    doc_file = tmp_path / "docs.md"
    doc_file.write_text(SAMPLE_DOC_EN, encoding="utf-8")

    steps = parse_doc(str(doc_file))

    # Annotation should contain the descriptive text
    assert "New Project" in steps[0].annotation
    assert "input field" in steps[1].annotation


def test_screenshot_paths(tmp_path: Path):
    doc_file = tmp_path / "docs.md"
    doc_file.write_text(SAMPLE_DOC_EN, encoding="utf-8")

    steps = parse_doc(str(doc_file))

    for i, step in enumerate(steps, 1):
        assert step.screenshot_path == f"screenshots/step_{i:02d}.png"
