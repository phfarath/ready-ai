"""Unit tests for the mutation harness: config loading, JS generation,
regime classification, scorecard math, and scorecard HTML rendering."""

import json
import re

import pytest

from src.docs.report_html import render_healer_scorecard
from tools.mutation_harness.mutations import load_config
from tools.mutation_harness.runner import _evaluate_apply
from tools.mutation_harness.scorecard import (
    MutationOutcome,
    compute_scorecard,
    regime_flagged,
)


def _write_config(tmp_path, body: str):
    p = tmp_path / "drill.yaml"
    p.write_text(body, encoding="utf-8")
    return p


VALID_YAML = """
app_url: http://127.0.0.1:9999/app
threshold: 0.9
settle_ms: 50
mutations:
  - id: rename-cta
    kind: semantic
    op: set_text
    selector: "#cta"
    value: "Confirm order"
  - id: drop-panel
    kind: semantic
    op: remove
    selector: "#promo"
  - id: move-panel
    kind: semantic
    op: move_after
    selector: "#details"
    to: "#footer"
  - id: aria-flip
    kind: semantic
    op: set_attribute
    selector: "#tab1"
    attr: "data-state"
    value: "open"
  - id: clock-churn
    kind: noise
    op: set_text
    selector: "#clock"
    value: "{{now}}"
  - id: promo-banner
    kind: noise
    op: insert_banner
    value: "Sale ends soon"
  - id: raw
    kind: noise
    js: "document.title = 'x-' + Math.random(); true"
"""


class TestLoadConfig:
    def test_valid_config(self, tmp_path):
        cfg = load_config(_write_config(tmp_path, VALID_YAML))
        assert cfg.app_url == "http://127.0.0.1:9999/app"
        assert cfg.threshold == 0.9
        assert cfg.settle_ms == 50
        assert len(cfg.mutations) == 7
        kinds = [m.kind for m in cfg.mutations]
        assert kinds.count("semantic") == 4
        assert kinds.count("noise") == 3

    def test_defaults(self, tmp_path):
        p = _write_config(
            tmp_path,
            "mutations:\n  - id: x\n    kind: noise\n    js: 'true'\n",
        )
        cfg = load_config(p)
        assert cfg.app_url == ""
        assert cfg.threshold == 0.95
        assert cfg.settle_ms == 150
        assert cfg.mutations[0].op == "js"

    def test_empty_mutations_rejected(self, tmp_path):
        p = _write_config(tmp_path, "app_url: http://x\nmutations: []\n")
        with pytest.raises(ValueError, match="non-empty list"):
            load_config(p)

    def test_bad_kind_rejected(self, tmp_path):
        p = _write_config(
            tmp_path,
            "mutations:\n  - id: x\n    kind: cosmetic\n    js: 'true'\n",
        )
        with pytest.raises(ValueError, match="kind"):
            load_config(p)

    def test_missing_selector_rejected(self, tmp_path):
        p = _write_config(
            tmp_path,
            "mutations:\n  - id: x\n    kind: semantic\n    op: remove\n",
        )
        with pytest.raises(ValueError, match="selector"):
            load_config(p)

    def test_missing_value_rejected(self, tmp_path):
        p = _write_config(
            tmp_path,
            "mutations:\n  - id: x\n    kind: semantic\n    op: set_text\n    selector: '#a'\n",
        )
        with pytest.raises(ValueError, match="value"):
            load_config(p)

    def test_set_attribute_requires_attr(self, tmp_path):
        p = _write_config(
            tmp_path,
            "mutations:\n  - id: x\n    kind: semantic\n    op: set_attribute\n"
            "    selector: '#a'\n    value: v\n",
        )
        with pytest.raises(ValueError, match="attr"):
            load_config(p)

    def test_duplicate_id_rejected(self, tmp_path):
        p = _write_config(
            tmp_path,
            "mutations:\n"
            "  - id: x\n    kind: noise\n    js: 'true'\n"
            "  - id: x\n    kind: noise\n    js: 'true'\n",
        )
        with pytest.raises(ValueError, match="duplicate"):
            load_config(p)

    def test_bad_threshold_rejected(self, tmp_path):
        p = _write_config(
            tmp_path,
            "threshold: 0\nmutations:\n  - id: x\n    kind: noise\n    js: 'true'\n",
        )
        with pytest.raises(ValueError, match="threshold"):
            load_config(p)

    def test_non_string_selector_rejected(self, tmp_path):
        p = _write_config(
            tmp_path,
            "mutations:\n  - id: x\n    kind: semantic\n    op: remove\n"
            "    selector: 123\n",
        )
        with pytest.raises(ValueError, match="'selector' must be a string"):
            load_config(p)

    def test_non_scalar_value_rejected(self, tmp_path):
        p = _write_config(
            tmp_path,
            "mutations:\n  - id: x\n    kind: semantic\n    op: set_text\n"
            "    selector: '#a'\n    value: {k: v}\n",
        )
        with pytest.raises(ValueError, match="'value' must be a scalar"):
            load_config(p)


class TestApplyJs:
    def _mut(self, tmp_path, op_block: str):
        p = _write_config(
            tmp_path,
            f"mutations:\n  - id: m1\n{op_block}",
        )
        return load_config(p).mutations[0]

    def test_set_text_escapes_strings(self, tmp_path):
        m = self._mut(
            tmp_path,
            '    kind: semantic\n    op: set_text\n    selector: "#b\\"uggy"\n'
            "    value: 'it''s \"quoted\"'\n",
        )
        js = m.apply_js()
        assert 'querySelector("#b\\"uggy")' in js
        assert '"it\'s \\"quoted\\""' in js
        assert js.endswith("return true;})()")

    def test_remove(self, tmp_path):
        m = self._mut(
            tmp_path,
            "    kind: semantic\n    op: remove\n    selector: '#gone'\n",
        )
        assert "el.remove()" in m.apply_js()

    def test_move_after(self, tmp_path):
        m = self._mut(
            tmp_path,
            "    kind: semantic\n    op: move_after\n    selector: '#a'\n    to: '#b'\n",
        )
        js = m.apply_js()
        assert "insertAdjacentElement('afterend',el)" in js
        assert '"#b"' in js

    def test_set_attribute(self, tmp_path):
        m = self._mut(
            tmp_path,
            "    kind: semantic\n    op: set_attribute\n    selector: '#t'\n"
            "    attr: data-state\n    value: open\n",
        )
        js = m.apply_js()
        assert 'setAttribute("data-state","open")' in js

    def test_insert_banner_default_target(self, tmp_path):
        m = self._mut(
            tmp_path,
            "    kind: noise\n    op: insert_banner\n    value: Hi\n",
        )
        js = m.apply_js()
        assert 'querySelector("body")' in js
        assert "ra-mutation-m1" in js
        assert "createElement('div')" in js

    def test_now_placeholder_substituted(self, tmp_path):
        m = self._mut(
            tmp_path,
            "    kind: noise\n    op: set_text\n    selector: '#c'\n    value: '{{now}}'\n",
        )
        js = m.apply_js()
        assert "{{now}}" not in js
        assert re.search(r'"\d{4}-\d{2}-\d{2}T', js)

    def test_rand_placeholder_differs_per_call(self, tmp_path):
        m = self._mut(
            tmp_path,
            "    kind: noise\n    op: set_text\n    selector: '#c'\n    value: '{{rand}}'\n",
        )
        assert m.apply_js() != m.apply_js()

    def test_raw_js_passthrough(self, tmp_path):
        m = self._mut(
            tmp_path,
            "    kind: noise\n    js: 'window.x=1; true'\n",
        )
        assert m.apply_js() == "window.x=1; true"


class TestRegimeFlagged:
    @pytest.mark.parametrize(
        "regime,visual,dom,expected",
        [
            ("single_visual", True, False, True),
            ("single_visual", False, True, False),
            ("single_dom", False, True, True),
            ("single_dom", True, False, False),
            ("gated", True, True, True),
            ("gated", True, False, False),
            ("gated", False, True, False),
            ("gated", False, False, False),
        ],
    )
    def test_truth_table(self, regime, visual, dom, expected):
        o = MutationOutcome(
            mutation_id="m", kind="semantic", applied=True,
            visual_flagged=visual, dom_flagged=dom,
        )
        assert regime_flagged(regime, o) is expected

    def test_unknown_regime_raises(self):
        o = MutationOutcome(mutation_id="m", kind="semantic", applied=True)
        with pytest.raises(ValueError, match="Unknown regime"):
            regime_flagged("single_network", o)


def _o(mid, kind, visual=False, dom=False, applied=True):
    return MutationOutcome(
        mutation_id=mid, kind=kind, applied=applied,
        visual_flagged=visual, dom_flagged=dom,
        visual_similarity=0.5 if visual else 1.0,
    )


class TestComputeScorecard:
    def test_metrics_math(self):
        outcomes = [
            _o("s1", "semantic", visual=True, dom=True),    # detected everywhere
            _o("s2", "semantic", visual=False, dom=True),   # dom-only → gated FN
            _o("s3", "semantic", visual=True, dom=False),   # visual-only → gated FN
            _o("n1", "noise", visual=False, dom=False),     # clean → TN all
            _o("n2", "noise", visual=True, dom=False),      # FP visual, TN gated
        ]
        sc = compute_scorecard(outcomes, app_url="u", threshold=0.95)

        g = sc["regimes"]["gated"]
        assert (g["tp"], g["fn"]) == (1, 2)
        assert (g["fp"], g["tn"]) == (0, 2)
        assert g["detection_rate"] == pytest.approx(1 / 3)
        assert g["fpr"] == 0.0
        assert g["mutation_score"] == g["detection_rate"]

        v = sc["regimes"]["single_visual"]
        assert (v["tp"], v["fn"]) == (2, 1)
        assert (v["fp"], v["tn"]) == (1, 1)
        assert v["fpr"] == 0.5

        d = sc["regimes"]["single_dom"]
        assert (d["tp"], d["fn"]) == (2, 1)
        assert (d["fp"], d["tn"]) == (0, 2)

    def test_unapplied_excluded_from_confusion(self):
        outcomes = [
            _o("s1", "semantic", visual=True, dom=True),
            MutationOutcome(
                mutation_id="s2", kind="semantic", applied=False,
                error="stale selector",
            ),
        ]
        sc = compute_scorecard(outcomes, app_url="u", threshold=0.95)
        assert sc["applied_mutations"] == 1
        assert sc["skipped_mutations"] == 1
        assert sc["regimes"]["gated"]["detection_rate"] == 1.0
        mut = {m["id"]: m for m in sc["mutations"]}
        assert mut["s2"]["verdicts"] == {}
        assert mut["s2"]["error"] == "stale selector"

    def test_zero_noise_fpr_is_none(self):
        sc = compute_scorecard(
            [_o("s1", "semantic", visual=True, dom=True)],
            app_url="u", threshold=0.95,
        )
        assert sc["regimes"]["gated"]["fpr"] is None
        assert sc["regimes"]["gated"]["detection_rate"] == 1.0

    def test_zero_semantic_detection_is_none(self):
        sc = compute_scorecard(
            [_o("n1", "noise")], app_url="u", threshold=0.95
        )
        assert sc["regimes"]["gated"]["detection_rate"] is None
        assert sc["regimes"]["gated"]["fpr"] == 0.0

    def test_json_serializable(self):
        sc = compute_scorecard(
            [_o("s1", "semantic", visual=True), _o("n1", "noise")],
            app_url="u", threshold=0.95,
        )
        json.dumps(sc)


class _FakeConn:
    def __init__(self, response):
        self._response = response

    async def send(self, method, params):
        assert method == "Runtime.evaluate"
        return self._response


class TestEvaluateApply:
    @pytest.mark.asyncio
    async def test_truthy_value_marks_applied(self):
        applied, error = await _evaluate_apply(
            _FakeConn({"result": {"type": "boolean", "value": True}}), "true"
        )
        assert applied is True
        assert error is None

    @pytest.mark.asyncio
    async def test_falsy_value_marks_unapplied(self):
        applied, error = await _evaluate_apply(
            _FakeConn({"result": {"type": "boolean", "value": False}}), "false"
        )
        assert applied is False
        assert error is None

    @pytest.mark.asyncio
    async def test_throwing_js_is_not_applied(self):
        response = {
            "result": {
                "type": "object",
                "subtype": "error",
                "description": "SyntaxError: Unexpected token",
            },
            "exceptionDetails": {
                "text": "Uncaught",
                "exception": {
                    "className": "SyntaxError",
                    "description": "SyntaxError: Unexpected token",
                },
            },
        }
        applied, error = await _evaluate_apply(_FakeConn(response), "[[[")
        assert applied is False
        assert "threw" in error
        assert "SyntaxError" in error

    @pytest.mark.asyncio
    async def test_exception_details_without_exception_object(self):
        response = {
            "result": {},
            "exceptionDetails": {"text": "Uncaught ReferenceError"},
        }
        applied, error = await _evaluate_apply(_FakeConn(response), "nope()")
        assert applied is False
        assert "ReferenceError" in error


class TestRenderHealerScorecard:
    def _scorecard(self):
        return compute_scorecard(
            [
                _o("rename-<b>x</b>", "semantic", visual=True, dom=True),
                _o("clock", "noise", visual=True, dom=False),
            ],
            app_url="http://app/?q=<script>",
            threshold=0.95,
        )

    def test_writes_html(self, tmp_path):
        out = tmp_path / "scorecard.html"
        result = render_healer_scorecard(self._scorecard(), out)
        html = out.read_text(encoding="utf-8")
        assert result == str(out)
        assert "Healer Scorecard" in html
        assert "Gated (visual AND DOM)" in html

    def test_gated_metrics_visible(self, tmp_path):
        out = tmp_path / "s.html"
        render_healer_scorecard(self._scorecard(), out)
        html = out.read_text(encoding="utf-8")
        # gated: 1 semantic detected → 100.0%; noise visual-only → FPR 0%
        assert "100.0%" in html
        assert "0.0%" in html

    def test_escapes_html(self, tmp_path):
        out = tmp_path / "s.html"
        render_healer_scorecard(self._scorecard(), out)
        html = out.read_text(encoding="utf-8")
        assert "<script>" not in html
        assert "rename-&lt;b&gt;x&lt;/b&gt;" in html
