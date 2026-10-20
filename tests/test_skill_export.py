"""US9 export de Agent Skills verificadas (experimental) — TDD RED.

Exporta docs.md verificado como Agent Skill (agentskills.io):
SKILL.md com frontmatter + screenshots em assets/ + skill.json
vinculando a ultima verificacao. Formato experimental: exige
READY_AI_EXPERIMENTAL=1.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.docs import export as export_mod

DOC_MD = """# Login Guide

## Step 1: Open the login page

![login](screenshots/step_01.png)

<details>
**Action executed**: navigate to https://app.test/login
</details>

Type your credentials to continue.

---

## Step 2: Submit the form

![submit](screenshots/step_02.png)

<details>
**Action executed**: click #submit
</details>

Welcome back.
"""

# Minimal valid PNG (1x1) — content irrelevant, copy is byte-wise.
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _fixture_run(tmp_path: Path) -> tuple[Path, Path]:
    doc = tmp_path / "docs.md"
    doc.write_text(DOC_MD, encoding="utf-8")
    shots = tmp_path / "screenshots"
    shots.mkdir()
    (shots / "step_01.png").write_bytes(PNG_BYTES)
    (shots / "step_02.png").write_bytes(PNG_BYTES)
    return doc, shots


def _enable_experimental(monkeypatch) -> None:
    monkeypatch.setenv("READY_AI_EXPERIMENTAL", "1")


def test_skill_format_registered():
    assert "skill" in export_mod.SUPPORTED_FORMATS


def test_skill_export_blocked_without_experimental_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("READY_AI_EXPERIMENTAL", raising=False)
    doc, shots = _fixture_run(tmp_path)
    try:
        export_mod.export_docs(doc, "skill", tmp_path / "out", shots)
    except ValueError as exc:
        assert "experimental" in str(exc).lower()
    else:
        raise AssertionError("skill export without flag should fail")


def test_skill_export_creates_valid_layout(tmp_path, monkeypatch):
    _enable_experimental(monkeypatch)
    doc, shots = _fixture_run(tmp_path)
    out = tmp_path / "out"
    created = export_mod.export_docs(doc, "skill", out, shots)
    skill_md = out / "login-guide" / "SKILL.md"
    assert skill_md.is_file()
    text = skill_md.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: login-guide" in text
    assert "description:" in text
    assert "## Step 1: Open the login page" in text
    assert "## Step 2: Submit the form" in text
    assert "./assets/step_01.png" in text
    assert (out / "login-guide" / "assets" / "step_01.png").is_file()
    assert (out / "login-guide" / "assets" / "step_02.png").is_file()
    assert skill_md in created.files_created


def test_skill_manifest_binds_verification(tmp_path, monkeypatch):
    _enable_experimental(monkeypatch)
    from src.docs.export import export_skill

    doc, shots = _fixture_run(tmp_path)
    out = tmp_path / "out"
    export_skill(
        doc,
        out,
        shots,
        run_id="run-42",
        app_version="2.3.1",
        verified_at="2026-10-20T00:00:00+00:00",
        result="passed",
    )
    manifest = json.loads((out / "login-guide" / "skill.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "run-42"
    assert manifest["app_version"] == "2.3.1"
    assert manifest["verified_at"] == "2026-10-20T00:00:00+00:00"
    assert manifest["result"] == "passed"
    assert manifest["steps"] == 2
    assert manifest["skill"] == "login-guide"


def test_round_trip_parse_export(tmp_path, monkeypatch):
    _enable_experimental(monkeypatch)
    from src.docs.parser import parse_doc

    doc, shots = _fixture_run(tmp_path)
    steps = parse_doc(doc)
    assert len(steps) == 2
    out = tmp_path / "out"
    export_mod.export_docs(doc, "skill", out, shots)
    skill_text = (out / "login-guide" / "SKILL.md").read_text(encoding="utf-8")
    for step in steps:
        assert step.title in skill_text


def test_cli_accepts_skill_format():
    import main as main_mod

    args = main_mod.parse_args(["export", "--run-id", "r1", "--format", "skill"])
    assert args.format == "skill"
