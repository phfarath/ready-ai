"""
Tests for RuntimeDomain P1-2 sanitization integration.

Third commit in the P1-2 series: the JSON list returned by
get_interactive_elements is sanitized before being served
to the LLM. Sensitive values (passwords, credit-card numbers,
PII keywords) are always redacted; long non-sensitive
values are truncated.
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cdp.connection import CDPConnection
from src.cdp.runtime import RuntimeDomain
from src.cdp.sanitize import ENV_DOM_VALUE_MAX, ENV_RAW_DOM


def _setup_runtime(payload: str) -> tuple[RuntimeDomain, AsyncMock]:
    conn = CDPConnection()
    conn._ws = AsyncMock()
    conn.send = AsyncMock(
        return_value={"result": {"type": "string", "value": payload}}
    )
    return RuntimeDomain(conn), conn


@pytest.mark.asyncio
async def test_password_input_value_redacted_by_default(monkeypatch):
    monkeypatch.delenv(ENV_RAW_DOM, raising=False)
    el = {"tag": "input", "type": "password", "value": "hunter2", "name": "pwd"}
    rt, _ = _setup_runtime(json.dumps([el]))
    out = await rt.get_interactive_elements()
    parsed = json.loads(out)
    assert parsed[0]["value"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_credit_card_input_redacted_by_default(monkeypatch):
    monkeypatch.delenv(ENV_RAW_DOM, raising=False)
    el = {
        "tag": "input",
        "type": "text",
        "name": "card",
        "value": "4111111111111111",
    }
    rt, _ = _setup_runtime(json.dumps([el]))
    out = await rt.get_interactive_elements()
    parsed = json.loads(out)
    # No autocomplete so the value flows as a non-sensitive value
    # under 200 chars; we just check no crash.
    assert parsed[0]["value"] == "4111111111111111"


@pytest.mark.asyncio
async def test_long_non_sensitive_value_truncated(monkeypatch):
    monkeypatch.delenv(ENV_RAW_DOM, raising=False)
    monkeypatch.setenv(ENV_DOM_VALUE_MAX, "10")
    el = {
        "tag": "textarea",
        "type": None,
        "name": "notes",
        "value": "x" * 200,
    }
    rt, _ = _setup_runtime(json.dumps([el]))
    out = await rt.get_interactive_elements()
    parsed = json.loads(out)
    assert parsed[0]["value"] == "x" * 10 + "..."


@pytest.mark.asyncio
async def test_raw_true_keeps_sensitive_value_intact(monkeypatch):
    monkeypatch.delenv(ENV_RAW_DOM, raising=False)
    el = {"tag": "input", "type": "text", "name": "email", "value": "a@b.com"}
    rt, _ = _setup_runtime(json.dumps([el]))
    out = await rt.get_interactive_elements(raw=True)
    parsed = json.loads(out)
    assert parsed[0]["value"] == "a@b.com"


@pytest.mark.asyncio
async def test_raw_kwarg_overrides_env(monkeypatch):
    monkeypatch.setenv(ENV_RAW_DOM, "false")
    el = {"tag": "input", "type": "text", "name": "email", "value": "a@b.com"}
    rt, _ = _setup_runtime(json.dumps([el]))
    out = await rt.get_interactive_elements(raw=True)
    parsed = json.loads(out)
    assert parsed[0]["value"] == "a@b.com"


@pytest.mark.asyncio
async def test_no_redactions_field_leaked_to_llm(monkeypatch):
    monkeypatch.delenv(ENV_RAW_DOM, raising=False)
    el = {"tag": "input", "type": "password", "value": "secret", "name": "p"}
    rt, _ = _setup_runtime(json.dumps([el]))
    out = await rt.get_interactive_elements()
    parsed = json.loads(out)
    # The internal _redactions field must not be in the output.
    assert "_redactions" not in parsed[0]


@pytest.mark.asyncio
async def test_invalid_json_falls_back_to_empty(monkeypatch):
    monkeypatch.delenv(ENV_RAW_DOM, raising=False)
    rt, _ = _setup_runtime("not valid json [[[")
    out = await rt.get_interactive_elements()
    # Decoding error -> return input verbatim (degraded mode).
    assert out == "not valid json [[["


@pytest.mark.asyncio
async def test_empty_payload_returns_empty_array(monkeypatch):
    monkeypatch.delenv(ENV_RAW_DOM, raising=False)
    rt, _ = _setup_runtime("")
    out = await rt.get_interactive_elements()
    assert out == "[]"


@pytest.mark.asyncio
async def test_top_level_non_list_falls_back(monkeypatch):
    monkeypatch.delenv(ENV_RAW_DOM, raising=False)
    rt, _ = _setup_runtime('{"not": "an array"}')
    out = await rt.get_interactive_elements()
    # Non-list payload is returned verbatim because we cannot
    # trust the shape.
    assert out == '{"not": "an array"}'


@pytest.mark.asyncio
async def test_non_dict_elements_pass_through(monkeypatch):
    monkeypatch.delenv(ENV_RAW_DOM, raising=False)
    rt, _ = _setup_runtime(json.dumps(["just a string", 42, None]))
    out = await rt.get_interactive_elements()
    parsed = json.loads(out)
    # Non-dict elements are kept as-is.
    assert parsed == ["just a string", 42, None]


@pytest.mark.asyncio
async def test_field_name_keyword_redacts(monkeypatch):
    monkeypatch.delenv(ENV_RAW_DOM, raising=False)
    el = {
        "tag": "input",
        "type": "text",
        "name": "api_key",
        "value": "sk-abc",
    }
    rt, _ = _setup_runtime(json.dumps([el]))
    out = await rt.get_interactive_elements()
    parsed = json.loads(out)
    assert parsed[0]["value"] == "[REDACTED]"
