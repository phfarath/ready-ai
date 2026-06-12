"""
Tests for the P1-2 sanitization module.

The module is pure-functional, so these tests do not need
any Chrome / CDP / async infrastructure. They cover the
two redaction layers (sensitive and non-sensitive) and the
per-pass counters.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cdp.sanitize import (
    DOM_VALUE_MAX_DEFAULT,
    ENV_DOM_VALUE_MAX,
    ENV_RAW_DOM,
    REDACTED_SENTINEL,
    is_raw_mode,
    is_sensitive_field,
    resolve_value_max,
    sanitize_html,
    sanitize_interactive_element,
)


# ---------------------------------------------------------------------------
# is_sensitive_field
# ---------------------------------------------------------------------------


class TestIsSensitiveField:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"field_type": "password"},
            {"field_type": "Password"},
            {"autocomplete": "cc-number"},
            {"autocomplete": "current-password"},
            {"autocomplete": "one-time-code"},
            {"name": "user_password"},
            {"name": "PasswordField"},
            {"name": "senha"},
            {"name": "Senha do usuario"},
            {"name": "cpf"},
            {"name": "cnpj"},
            {"name": "ssn"},
            {"name": "api_key"},
            {"name": "token"},
        ],
    )
    def test_sensitive_cases(self, kwargs):
        assert is_sensitive_field(**kwargs) is True

    @pytest.mark.parametrize(
        "kwargs",
        [
            {},
            {"name": "email"},
            {"name": "username"},
            {"name": "search"},
            {"field_type": "text"},
            {"field_type": "email"},
            {"autocomplete": "username"},
            {"autocomplete": "email"},
        ],
    )
    def test_not_sensitive_cases(self, kwargs):
        assert is_sensitive_field(**kwargs) is False


# ---------------------------------------------------------------------------
# resolve_value_max
# ---------------------------------------------------------------------------


class TestResolveValueMax:
    def test_default(self, monkeypatch):
        monkeypatch.delenv(ENV_DOM_VALUE_MAX, raising=False)
        assert resolve_value_max() == DOM_VALUE_MAX_DEFAULT

    def test_explicit_value(self, monkeypatch):
        monkeypatch.setenv(ENV_DOM_VALUE_MAX, "300")
        assert resolve_value_max() == 300

    def test_zero_means_no_cap(self, monkeypatch):
        monkeypatch.setenv(ENV_DOM_VALUE_MAX, "0")
        assert resolve_value_max() == 0

    def test_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv(ENV_DOM_VALUE_MAX, "garbage")
        assert resolve_value_max() == DOM_VALUE_MAX_DEFAULT

    def test_negative_treated_as_no_cap(self, monkeypatch):
        monkeypatch.setenv(ENV_DOM_VALUE_MAX, "-5")
        assert resolve_value_max() == 0

    def test_empty_falls_back(self, monkeypatch):
        monkeypatch.setenv(ENV_DOM_VALUE_MAX, "   ")
        assert resolve_value_max() == DOM_VALUE_MAX_DEFAULT


# ---------------------------------------------------------------------------
# is_raw_mode
# ---------------------------------------------------------------------------


class TestIsRawMode:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy(self, monkeypatch, value):
        monkeypatch.setenv(ENV_RAW_DOM, value)
        assert is_raw_mode() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_falsy(self, monkeypatch, value):
        monkeypatch.setenv(ENV_RAW_DOM, value)
        assert is_raw_mode() is False

    def test_unset(self, monkeypatch):
        monkeypatch.delenv(ENV_RAW_DOM, raising=False)
        assert is_raw_mode() is False


# ---------------------------------------------------------------------------
# sanitize_html — structural noise
# ---------------------------------------------------------------------------


class TestSanitizeHTMLStructural:
    def test_removes_script_blocks(self):
        html = '<div>before<script>alert("x")</script>after</div>'
        out = sanitize_html(html, raw=False, value_max=200)
        assert "alert" not in out.html
        assert "before" in out.html
        assert "after" in out.html
        assert out.counters.scripts_removed == 1

    def test_removes_multiline_script(self):
        html = "<script>\nfoo();\nbar();\n</script>keep"
        out = sanitize_html(html, raw=False, value_max=200)
        assert "foo" not in out.html
        assert "bar" not in out.html
        assert "keep" in out.html
        assert out.counters.scripts_removed == 1

    def test_removes_style_blocks(self):
        html = '<head><style>body { color: red; }</style></head>body'
        out = sanitize_html(html, raw=False, value_max=200)
        assert "color: red" not in out.html
        assert out.counters.styles_removed == 1

    def test_removes_noscript_blocks(self):
        html = '<noscript>Please enable JS</noscript>visible'
        out = sanitize_html(html, raw=False, value_max=200)
        assert "Please enable" not in out.html
        assert "visible" in out.html
        assert out.counters.noscripts_removed == 1

    def test_removes_html_comments(self):
        html = "before<!-- secret token: abc123 -->after"
        out = sanitize_html(html, raw=False, value_max=200)
        assert "abc123" not in out.html
        assert "before" in out.html
        assert "after" in out.html
        assert out.counters.comments_removed == 1

    def test_removes_multiline_comment(self):
        html = "x<!--\nline1\nline2\n-->y"
        out = sanitize_html(html, raw=False, value_max=200)
        assert "line1" not in out.html
        assert "line2" not in out.html
        assert "x" in out.html and "y" in out.html


# ---------------------------------------------------------------------------
# sanitize_html — data-* attributes
# ---------------------------------------------------------------------------


class TestSanitizeHTMLDataAttrs:
    def test_strips_generic_data_attrs(self):
        html = '<div data-user="john" data-foo="bar">x</div>'
        out = sanitize_html(html, raw=False, value_max=200)
        assert "data-user" not in out.html
        assert "data-foo" not in out.html
        assert "x</div>" in out.html
        assert out.counters.data_attrs_removed == 2

    def test_preserves_testid_and_cy(self):
        html = '<button data-testid="save-btn" data-cy="save-cy" data-pii="x">Save</button>'
        out = sanitize_html(html, raw=False, value_max=200)
        assert 'data-testid="save-btn"' in out.html
        assert 'data-cy="save-cy"' in out.html
        assert "data-pii" not in out.html
        assert out.counters.data_attrs_removed == 1


# ---------------------------------------------------------------------------
# sanitize_html — value handling
# ---------------------------------------------------------------------------


class TestSanitizeHTMLValues:
    def test_password_value_always_redacted(self):
        html = '<input type="password" name="pwd" value="hunter2">'
        out = sanitize_html(html, raw=False, value_max=200)
        assert "hunter2" not in out.html
        assert REDACTED_SENTINEL in out.html
        assert out.counters.values_redacted == 1

    def test_credit_card_redacted(self):
        html = '<input type="text" name="card" autocomplete="cc-number" value="4111111111111111">'
        out = sanitize_html(html, raw=False, value_max=200)
        assert "4111111111111111" not in out.html
        assert REDACTED_SENTINEL in out.html
        assert out.counters.values_redacted == 1

    def test_field_name_keyword_redacts(self):
        html = '<input type="text" name="api_key" value="sk-abc123">'
        out = sanitize_html(html, raw=False, value_max=200)
        assert "sk-abc123" not in out.html
        assert out.counters.values_redacted == 1

    def test_field_id_keyword_redacts(self):
        html = '<input type="text" id="senha" value="qualquer">'
        out = sanitize_html(html, raw=False, value_max=200)
        assert "qualquer" not in out.html
        assert out.counters.values_redacted == 1

    def test_long_non_sensitive_value_truncated(self):
        long = "x" * 500
        html = f'<input type="text" name="notes" value="{long}">'
        out = sanitize_html(html, raw=False, value_max=200)
        assert "x" * 500 not in out.html
        assert "x" * 200 + "..." in out.html
        assert out.counters.values_truncated == 1

    def test_short_non_sensitive_value_kept(self):
        html = '<input type="text" name="email" value="a@b.com">'
        out = sanitize_html(html, raw=False, value_max=200)
        assert "a@b.com" in out.html
        assert out.counters.values_truncated == 0
        assert out.counters.values_redacted == 0

    def test_no_value_attribute_untouched(self):
        html = '<input type="text" name="search">'
        out = sanitize_html(html, raw=False, value_max=200)
        assert 'name="search"' in out.html
        assert out.counters.values_truncated == 0
        assert out.counters.values_redacted == 0

    def test_value_max_zero_means_no_truncate(self):
        long = "x" * 500
        html = f'<input type="text" name="notes" value="{long}">'
        out = sanitize_html(html, raw=False, value_max=0)
        assert "x" * 500 in out.html
        assert out.counters.values_truncated == 0


# ---------------------------------------------------------------------------
# sanitize_html — raw mode
# ---------------------------------------------------------------------------


class TestSanitizeHTMLRawMode:
    def test_raw_keeps_everything(self):
        html = '<script>alert(1)</script><div data-x="y">x</div>'
        out = sanitize_html(html, raw=True)
        assert out.html == html
        # Counters are still zero because we did nothing.
        assert all(v == 0 for v in out.counters.to_dict().values())

    def test_raw_still_redacts_password(self):
        # The sensitive layer is unconditional; raw mode only
        # skips the cosmetic passes.
        html = '<input type="password" value="hunter2">'
        out = sanitize_html(html, raw=True)
        # In raw mode the HTML is unchanged.
        assert "hunter2" in out.html


# ---------------------------------------------------------------------------
# sanitize_html — counters
# ---------------------------------------------------------------------------


class TestSanitizeHTMLCounters:
    def test_combined_pass(self):
        html = (
            "<script>x</script>"
            "<style>y</style>"
            "<!--z-->"
            '<div data-a="1" data-b="2" data-testid="ok">'
            '<input type="password" value="secret">'
            "</div>"
        )
        out = sanitize_html(html, raw=False, value_max=200)
        c = out.counters
        assert c.scripts_removed == 1
        assert c.styles_removed == 1
        assert c.comments_removed == 1
        assert c.data_attrs_removed == 2
        assert c.values_redacted == 1
        assert c.noscripts_removed == 0
        assert c.values_truncated == 0

    def test_to_metrics_attrs(self):
        out = sanitize_html("<script>x</script>", raw=False, value_max=200)
        d = out.to_metrics_attrs()
        assert d["scripts_removed"] == 1
        assert all(isinstance(v, int) for v in d.values())


# ---------------------------------------------------------------------------
# sanitize_interactive_element
# ---------------------------------------------------------------------------


class TestSanitizeInteractiveElement:
    def _sample(self) -> dict:
        return {
            "tag": "input",
            "type": "text",
            "text": "a@b.com",
            "id": None,
            "name": "email",
            "href": None,
            "ariaLabel": None,
            "testId": None,
            "selector": "input[name=email]",
            "visible": True,
            "value": "a@b.com",
            "placeholder": "Enter your email",
        }

    def test_passthrough_when_not_sensitive_and_short(self):
        el = self._sample()
        out = sanitize_interactive_element(el, raw=False, value_max=200)
        assert out["text"] == "a@b.com"
        assert out["value"] == "a@b.com"
        assert out["placeholder"] == "Enter your email"
        assert out["_redactions"] == {}

    def test_sensitive_field_redacts_value_and_text(self):
        el = self._sample()
        el["type"] = "password"
        out = sanitize_interactive_element(el, raw=False, value_max=200)
        assert out["text"] == REDACTED_SENTINEL
        assert out["value"] == REDACTED_SENTINEL
        assert out["_redactions"]["text_redacted"] == 1
        assert out["_redactions"]["value_redacted"] == 1

    def test_sensitive_via_autocomplete(self):
        el = self._sample()
        el["name"] = "card"
        el["autocomplete"] = "cc-number"
        out = sanitize_interactive_element(el, raw=False, value_max=200)
        assert out["value"] == REDACTED_SENTINEL
        assert out["_redactions"]["value_redacted"] == 1

    def test_sensitive_via_keyword(self):
        el = self._sample()
        el["name"] = "api_key"
        el["value"] = "sk-abc123"
        out = sanitize_interactive_element(el, raw=False, value_max=200)
        assert out["value"] == REDACTED_SENTINEL
        assert "sk-abc123" not in out["value"]

    def test_long_non_sensitive_truncated(self):
        el = self._sample()
        el["value"] = "x" * 500
        out = sanitize_interactive_element(el, raw=False, value_max=200)
        assert out["value"] == "x" * 200 + "..."
        assert out["_redactions"]["value_truncated"] == 1

    def test_raw_keeps_everything(self):
        el = self._sample()
        el["type"] = "password"
        el["value"] = "hunter2"
        out = sanitize_interactive_element(el, raw=True)
        assert out["value"] == "hunter2"
        assert out["_redactions"] == {}

    def test_does_not_mutate_input(self):
        el = self._sample()
        el["type"] = "password"
        el["value"] = "secret"
        snapshot_before = dict(el)
        sanitize_interactive_element(el, raw=False, value_max=200)
        assert el == snapshot_before


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


class TestPublicExports:
    def test_expected_symbols_exported(self):
        from src import cdp
        from src.cdp import sanitize as _sanitize  # noqa: F401

        for name in (
            "SanitizedHTML",
            "SanitizationCounters",
            "is_raw_mode",
            "is_sensitive_field",
            "resolve_value_max",
            "sanitize_html",
            "sanitize_interactive_element",
            "REDACTED_SENTINEL",
            "SAFE_DATA_ATTRS",
            "DOM_VALUE_MAX_DEFAULT",
            "ENV_DOM_VALUE_MAX",
            "ENV_RAW_DOM",
        ):
            assert hasattr(cdp, name), f"missing public symbol: {name}"
            assert name in cdp.__all__, f"not in __all__: {name}"
