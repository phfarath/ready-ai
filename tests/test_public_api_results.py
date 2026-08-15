"""Structured, sanitized run results (READY-AI-T-13, DoD 3).

The API returns a structured and sanitized result carrying ``run_id``,
``status``, ``steps`` and only allowed ``artifacts``. Sensitive material
(typed text, URL credentials) must never survive into the public model.
"""

from __future__ import annotations

from pathlib import Path

from ready_ai import RunResult, RunStep

_SECRET_URL = "https://user:supersecret@app.example.com/dashboard"


def _flow_result(run_id="flow-abc123", url="https://app.example.com/start"):
    return {
        "run_id": run_id,
        "flow": "smoke",
        "url": url,
        "status": "failed",
        "steps": [
            {
                "index": 1,
                "name": "Login",
                "actions": [
                    {
                        "action": "type",
                        "params": {"selector": "#user", "text": "***"},
                        "description": "Typed text into #user",
                        "attempts": 1,
                        "passed": True,
                        "failure_reason": "",
                    },
                    {
                        "action": "click",
                        "params": {"selector": "#submit"},
                        "description": "Clicked element: #submit",
                        "attempts": 1,
                        "passed": True,
                        "failure_reason": "",
                    },
                ],
                "asserts": [
                    {
                        "type": "url_contains",
                        "selector": None,
                        "expected": "/dashboard",
                        "actual": _SECRET_URL,
                        "passed": False,
                        "message": "url_contains failed",
                    }
                ],
                "extracted": [{"name": "title", "selector": "h1", "value": "Dashboard"}],
                "attempts": 1,
                "status": "failed",
                "failure_reason": "url_contains failed",
                "skipped_asserts": 0,
                "skipped_extractions": 0,
            }
        ],
        "summary": {"steps_total": 1, "steps_passed": 0, "steps_failed": 1},
        "failure_reason": None,
    }


class TestRunResultMapping:
    def test_maps_run_id_status_steps_and_summary(self, tmp_path):
        result = RunResult.from_flow_result(_flow_result(), output_dir=tmp_path)

        assert isinstance(result, RunResult)
        assert result.run_id == "flow-abc123"
        assert result.status == "failed"
        assert result.flow == "smoke"
        assert len(result.steps) == 1
        assert result.summary["steps_total"] == 1
        assert result.failure_reason is None

        step = result.steps[0]
        assert step.index == 1
        assert step.name == "Login"
        assert step.status == "failed"
        assert step.attempts == 1
        assert len(step.actions) == 2
        assert len(step.asserts) == 1
        assert step.extracted[0]["value"] == "Dashboard"

    def test_result_is_json_serializable(self, tmp_path):
        result = RunResult.from_flow_result(_flow_result(), output_dir=tmp_path)
        import json

        payload = json.loads(result.model_dump_json())
        assert payload["run_id"] == "flow-abc123"
        assert payload["status"] == "failed"
        assert payload["steps"][0]["index"] == 1


class TestResultSanitization:
    def test_url_credentials_never_serialize(self, tmp_path):
        result = RunResult.from_flow_result(_flow_result(), output_dir=tmp_path)

        import json

        dumped = json.dumps(result.model_dump())
        assert "supersecret" not in dumped
        assert "user:" not in dumped
        # The actual URL is preserved but without its userinfo.
        assert "https://app.example.com/dashboard" in dumped

    def test_typed_text_stays_masked(self, tmp_path):
        result = RunResult.from_flow_result(_flow_result(), output_dir=tmp_path)
        typed = result.steps[0].actions[0]
        assert typed["params"]["text"] == "***"

    def test_result_url_userinfo_stripped(self, tmp_path):
        result = RunResult.from_flow_result(
            _flow_result(url=_SECRET_URL), output_dir=tmp_path
        )
        assert result.url == "https://app.example.com/dashboard"
        assert "supersecret" not in result.url

    def test_sanitization_is_recursive_through_steps(self, tmp_path):
        result = RunResult.from_flow_result(_flow_result(), output_dir=tmp_path)
        for step in result.steps:
            for assertion in step.asserts:
                assert "supersecret" not in str(assertion.get("actual"))


class TestAllowedArtifacts:
    def test_artifacts_only_include_written_files_inside_output_dir(self, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        (out / "flow-abc123_flow_result.json").write_text("{}", encoding="utf-8")
        (out / "flow-abc123_flow_metrics.json").write_text("{}", encoding="utf-8")
        # Decoys: outside output_dir and a non-matching file inside it.
        (tmp_path / "flow-abc123_flow_result.json").write_text("{}", encoding="utf-8")
        (out / "other_run_flow_result.json").write_text("{}", encoding="utf-8")
        (out / "flow-abc123_state.json").write_text("{}", encoding="utf-8")

        result = RunResult.from_flow_result(_flow_result(), output_dir=out)

        assert sorted(Path(a).name for a in result.artifacts) == [
            "flow-abc123_flow_metrics.json",
            "flow-abc123_flow_result.json",
        ]
        for artifact in result.artifacts:
            resolved = Path(artifact).resolve()
            assert out.resolve() in resolved.parents

    def test_no_artifacts_when_nothing_written(self, tmp_path):
        result = RunResult.from_flow_result(_flow_result(), output_dir=tmp_path)
        assert result.artifacts == []


class TestStepModel:
    def test_step_accepts_engine_step_dict(self):
        step = RunStep.model_validate(
            {
                "index": 2,
                "name": None,
                "actions": [],
                "asserts": [],
                "extracted": [],
                "attempts": 1,
                "status": "passed",
                "failure_reason": "",
                "skipped_asserts": 0,
                "skipped_extractions": 0,
            }
        )
        assert step.index == 2
        assert step.status == "passed"
