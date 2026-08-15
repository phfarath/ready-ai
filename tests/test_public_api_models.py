"""Public SDK config models (READY-AI-T-13, DoD 2).

Models must validate URL, timeouts, effect policy and profile references
and must never serialize secrets (profiles are references, not cookie
payloads). Models are serializable and versionable: a ``version`` field,
lenient parsing (unknown keys ignored) for forward compatibility.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ready_ai import (
    BrowserOptions,
    EffectPolicy,
    Flow,
    FlowAction,
    FlowStep,
)
from ready_ai.models import SCHEMA_VERSION

_CRED_URL = "https://user:pass@example.com"


def _flow_with_actions(action_types, **flow_kwargs):
    actions = [FlowAction(action=t) for t in action_types]
    return Flow(
        url="https://app.example.com/start",
        steps=[FlowStep(name="s1", actions=actions)],
        **flow_kwargs,
    )


# ─── URL validation ───────────────────────────────────────────────────────


class TestUrlValidation:
    @pytest.mark.parametrize(
        "bad_url",
        [
            "",
            "not a url",
            "example.com/no-scheme",
            "javascript:alert(1)",
            "file:///etc/passwd",
            "ftp://example.com/file",
            "https://",  # no host
            "http://",
            _CRED_URL,  # credentials embedded in the URL would serialize a secret
        ],
    )
    def test_rejects_invalid_url(self, bad_url):
        with pytest.raises(ValidationError):
            Flow(url=bad_url, steps=[FlowStep()])

    def test_accepts_http_and_https(self):
        for url in (
            "https://app.example.com",
            "https://app.example.com/checkout/cart?q=1",
            "http://localhost:9222",
        ):
            flow = Flow(url=url, steps=[FlowStep()])
            assert flow.url == url


# ─── Timeout and retry budget validation ──────────────────────────────────


class TestTimeoutValidation:
    def test_rejects_zero_timeout(self):
        with pytest.raises(ValidationError):
            Flow(url="https://app.example.com", steps=[FlowStep()], timeout_s=0)

    def test_rejects_negative_timeout(self):
        with pytest.raises(ValidationError):
            Flow(url="https://app.example.com", steps=[FlowStep()], timeout_s=-1.5)

    def test_accepts_positive_timeout(self):
        flow = Flow(url="https://app.example.com", steps=[FlowStep()], timeout_s=0.25)
        assert flow.timeout_s == 0.25

    def test_rejects_negative_retries(self):
        with pytest.raises(ValidationError):
            Flow(url="https://app.example.com", steps=[FlowStep()], retries=-1)

    def test_zero_retries_is_valid_single_attempt(self):
        flow = Flow(url="https://app.example.com", steps=[FlowStep()], retries=0)
        assert flow.retries == 0


# ─── Effect policy validation ─────────────────────────────────────────────


class TestEffectPolicyValidation:
    def test_observe_rejects_click(self):
        with pytest.raises(ValidationError, match="effect_policy"):
            _flow_with_actions(["click"], effect_policy=EffectPolicy.OBSERVE)

    def test_observe_rejects_navigate(self):
        with pytest.raises(ValidationError, match="effect_policy"):
            _flow_with_actions(["navigate"], effect_policy="observe")

    def test_navigate_rejects_type(self):
        with pytest.raises(ValidationError, match="effect_policy"):
            _flow_with_actions(
                ["navigate", "type"], effect_policy=EffectPolicy.NAVIGATE
            )

    def test_observe_allows_read_only_actions(self):
        flow = _flow_with_actions(
            ["observe", "wait"], effect_policy=EffectPolicy.OBSERVE
        )
        assert flow.effect_policy == EffectPolicy.OBSERVE

    def test_navigate_allows_scroll(self):
        flow = _flow_with_actions(
            ["navigate", "scroll", "scroll_to", "wait"],
            effect_policy=EffectPolicy.NAVIGATE,
        )
        assert flow.effect_policy == EffectPolicy.NAVIGATE

    def test_interactive_allows_mutating_actions(self):
        flow = _flow_with_actions(
            ["click", "click_text", "type", "press_key"],
            effect_policy=EffectPolicy.INTERACTIVE,
        )
        assert flow.effect_policy == EffectPolicy.INTERACTIVE

    def test_unknown_policy_string_rejected(self):
        with pytest.raises(ValidationError):
            _flow_with_actions(["observe"], effect_policy="nuclear")

    def test_policy_validation_scans_every_step(self):
        with pytest.raises(ValidationError, match="effect_policy"):
            Flow(
                url="https://app.example.com",
                steps=[
                    FlowStep(name="read", actions=[FlowAction(action="observe")]),
                    FlowStep(actions=[FlowAction(action="click")]),
                ],
                effect_policy=EffectPolicy.OBSERVE,
            )

    def test_steps_min_length_one(self):
        with pytest.raises(ValidationError):
            Flow(url="https://app.example.com", steps=[])


# ─── Profile references: never secrets, validated references ──────────────


class TestBrowserOptionsProfile:
    def test_profile_is_a_reference_not_a_secret(self):
        opts = BrowserOptions(profile="alice")
        dumped = opts.model_dump()
        # The serialized model contains only the reference, never cookie payloads.
        assert dumped["profile"] == "alice"
        assert opts.profile == "alice"
        assert "cookies" not in json.dumps(dumped).lower()

    def test_rejects_path_traversal_reference(self):
        with pytest.raises(ValidationError, match="profile"):
            BrowserOptions(profile="../etc/passwd")

    def test_rejects_nested_dotdot_segment(self):
        with pytest.raises(ValidationError, match="profile"):
            BrowserOptions(profile="profiles/../../etc/passwd")

    def test_rejects_spaces_and_control_characters(self):
        with pytest.raises(ValidationError, match="profile"):
            BrowserOptions(profile="a b")
        with pytest.raises(ValidationError):
            BrowserOptions(profile="alice\nbob")

    def test_port_bounds(self):
        with pytest.raises(ValidationError):
            BrowserOptions(port=0)
        with pytest.raises(ValidationError):
            BrowserOptions(port=70000)
        assert BrowserOptions(port=9222).port == 9222


# ─── Serialization and versioning (forward compatibility) ─────────────────


class TestSerializationAndVersioning:
    def test_flow_serializes_with_schema_version(self):
        flow = Flow(url="https://app.example.com", steps=[FlowStep(name="s1")])
        dumped = flow.model_dump()
        assert dumped["version"] == SCHEMA_VERSION == 1
        assert dumped["url"] == "https://app.example.com"
        assert dumped["steps"][0]["name"] == "s1"

    def test_json_round_trip(self):
        flow = Flow(
            url="https://app.example.com",
            timeout_s=12.5,
            steps=[FlowStep(name="s1", actions=[FlowAction(action="observe")])],
        )
        raw = flow.model_dump_json()
        assert '"version":1' in raw
        again = Flow.model_validate_json(raw)
        assert again.model_dump() == flow.model_dump()

    def test_future_version_document_parses_leniently(self):
        # Forward-compat strategy: unknown keys ignored, newer version tolerated.
        flow = Flow.model_validate(
            {
                "version": 99,
                "future_top_level_key": "ignored",
                "url": "https://app.example.com",
                "steps": [
                    {
                        "name": "s1",
                        "future_step_key": {"a": 1},
                        "actions": [
                            {"action": "observe", "future_action_key": "x"}
                        ],
                    }
                ],
            }
        )
        assert flow.version == 99
        assert flow.url == "https://app.example.com"
        assert len(flow.steps) == 1

    def test_flow_has_no_secret_fields_whatsoever(self):
        flow = Flow(url="https://app.example.com", steps=[FlowStep()])
        dumped = flow.model_dump()
        for secret_key in ("cookies", "cookie", "password", "username", "token"):
            assert secret_key not in dumped

    def test_browser_options_serialization_is_versioned(self):
        dumped = BrowserOptions(headless=False, port=9444).model_dump()
        assert dumped == {
            "version": 1,
            "headless": False,
            "port": 9444,
            "profile": None,
        }

    def test_flow_action_keeps_forward_extra_params(self):
        # Action params are an open-ended extensibility surface (forward compat).
        step = FlowStep(
            actions=[
                FlowAction(action="type", selector="#user", text="alice", custom="x")
            ]
        )
        dumped = step.actions[0].model_dump(exclude_none=True)
        assert dumped["action"] == "type"
        assert dumped["custom"] == "x"
