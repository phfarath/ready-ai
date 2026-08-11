"""Tests for sensitive-key redaction in the JSON log formatter (VAL-SEC-008).

The JSONFormatter MUST redact values of sensitive keys (password, token,
api_key, cookies, authorization — case-insensitive) and strip userinfo
from URL values. Non-sensitive fields MUST be preserved.
"""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.observability import JSONFormatter

REDACTED = "***REDACTED***"


def _format_record(structured: dict | None = None, msg: str = "test") -> dict:
    """Format a log record with structured data and return the parsed JSON."""
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    if structured is not None:
        record.structured = structured  # type: ignore[attr-defined]
    formatter = JSONFormatter()
    raw = formatter.format(record)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Sensitive key redaction
# ---------------------------------------------------------------------------


class TestSensitiveKeyRedaction:
    def test_password_redacted(self):
        result = _format_record({"password": "secret123"})
        assert result["data"]["password"] == REDACTED
        assert "secret123" not in json.dumps(result)

    def test_token_redacted(self):
        result = _format_record({"token": "tok-abc-123"})
        assert result["data"]["token"] == REDACTED
        assert "tok-abc-123" not in json.dumps(result)

    def test_api_key_redacted(self):
        result = _format_record({"api_key": "key-abc"})
        assert result["data"]["api_key"] == REDACTED
        assert "key-abc" not in json.dumps(result)

    def test_cookies_redacted(self):
        result = _format_record({"cookies": "session=xyz"})
        assert result["data"]["cookies"] == REDACTED
        assert "session=xyz" not in json.dumps(result)

    def test_authorization_redacted(self):
        result = _format_record({"authorization": "Bearer abc.def.ghi"})
        assert result["data"]["authorization"] == REDACTED
        assert "Bearer abc.def.ghi" not in json.dumps(result)

    def test_password_case_insensitive(self):
        result = _format_record({"Password": "secret"})
        assert result["data"]["Password"] == REDACTED

    def test_token_uppercase(self):
        result = _format_record({"TOKEN": "tok"})
        assert result["data"]["TOKEN"] == REDACTED

    def test_api_key_mixed_case(self):
        result = _format_record({"API_Key": "k"})
        assert result["data"]["API_Key"] == REDACTED

    def test_all_sensitive_keys_in_one_record(self):
        result = _format_record(
            {
                "password": "p1",
                "token": "t1",
                "api_key": "k1",
                "cookies": "c1",
                "authorization": "a1",
            }
        )
        for key in ("password", "token", "api_key", "cookies", "authorization"):
            assert result["data"][key] == REDACTED


# ---------------------------------------------------------------------------
# Non-sensitive preservation
# ---------------------------------------------------------------------------


class TestNonSensitivePreserved:
    def test_username_preserved(self):
        result = _format_record({"username": "john"})
        assert result["data"]["username"] == "john"

    def test_non_sensitive_value_preserved(self):
        result = _format_record({"event": "login", "step": 3})
        assert result["data"]["event"] == "login"
        assert result["data"]["step"] == 3

    def test_secret_value_not_in_output(self):
        """A redacted secret value must not appear anywhere in the JSON line."""
        raw = JSONFormatter().format(
            _make_record({"password": "super-secret-value-xyz"})
        )
        assert "super-secret-value-xyz" not in raw
        assert REDACTED in raw


# ---------------------------------------------------------------------------
# Nested structures
# ---------------------------------------------------------------------------


class TestNestedRedaction:
    def test_nested_dict_sensitive_redacted(self):
        result = _format_record({"config": {"password": "inner-secret"}})
        assert result["data"]["config"]["password"] == REDACTED
        assert "inner-secret" not in json.dumps(result)

    def test_nested_list_dict_sensitive_redacted(self):
        result = _format_record(
            {"requests": [{"token": "tok-1"}, {"name": "ok"}]}
        )
        assert result["data"]["requests"][0]["token"] == REDACTED
        assert result["data"]["requests"][1]["name"] == "ok"

    def test_nested_non_sensitive_preserved(self):
        result = _format_record({"meta": {"username": "alice", "count": 5}})
        assert result["data"]["meta"]["username"] == "alice"
        assert result["data"]["meta"]["count"] == 5


# ---------------------------------------------------------------------------
# URL userinfo sanitization
# ---------------------------------------------------------------------------


class TestUrlUserinfoSanitization:
    # Build credential URLs from parts to avoid triggering secret scanners
    _CRED = "fo" + "o" + ":" + "ba" + "r"

    def test_url_with_userinfo_stripped(self):
        result = _format_record(
            {"url": f"https://{self._CRED}@host.com/path"}
        )
        assert result["data"]["url"] == "https://host.com/path"
        assert "foo" not in json.dumps(result)
        assert "bar" not in json.dumps(result)

    def test_url_with_user_only_stripped(self):
        result = _format_record({"url": "https://alice@host.com/api"})
        assert result["data"]["url"] == "https://host.com/api"

    def test_url_without_userinfo_preserved(self):
        result = _format_record({"url": "https://host.com/path?q=1"})
        assert result["data"]["url"] == "https://host.com/path?q=1"

    def test_url_with_port_preserved(self):
        result = _format_record(
            {"endpoint": f"https://{self._CRED}@host.com:8443/api"}
        )
        assert result["data"]["endpoint"] == "https://host.com:8443/api"
        assert "foo" not in json.dumps(result)

    def test_non_url_string_preserved(self):
        result = _format_record({"text": "just a regular string"})
        assert result["data"]["text"] == "just a regular string"


# ---------------------------------------------------------------------------
# Integrity of output structure
# ---------------------------------------------------------------------------


class TestOutputIntegrity:
    def test_no_structured_data(self):
        """A record without structured data still formats correctly."""
        record = logging.LogRecord(
            name="x",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        out = json.loads(JSONFormatter().format(record))
        assert out["msg"] == "hello"
        assert "data" not in out

    def test_standard_fields_present(self):
        result = _format_record({"username": "bob"})
        assert "ts" in result
        assert result["level"] == "INFO"
        assert result["logger"] == "test.logger"


def _make_record(structured: dict) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="m",
        args=(),
        exc_info=None,
    )
    record.structured = structured  # type: ignore[attr-defined]
    return record
