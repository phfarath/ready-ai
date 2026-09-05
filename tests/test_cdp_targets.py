"""Unit tests for the explicit TargetRegistry (READY-AI-T-PH2B).

No Chrome needed: the registry is pure, and the recv-loop hijack test
drives _recv_loop with a fake socket.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.cdp.connection import CDPConnection, TargetRegistry


def _registry() -> TargetRegistry:
    reg = TargetRegistry()
    reg.register("t-main", "s-main", type="page", url="https://app.example.com/spa")
    reg.register("t-popup", "s-popup", type="page", url="https://app.example.com/popup")
    return reg


def test_resolve_by_index_url_and_id():
    reg = _registry()
    assert reg.resolve(0).target_id == "t-main"
    assert reg.resolve(1).target_id == "t-popup"
    assert reg.resolve("t-popup").session_id == "s-popup"
    assert reg.resolve("/popup").target_id == "t-popup"


def test_resolve_unknown_names_context():
    reg = _registry()
    with pytest.raises(KeyError, match="unknown tab 'nope'"):
        reg.resolve("nope")
    try:
        reg.resolve("nope")
    except KeyError as exc:
        assert "t-main" in str(exc) or "/spa" in str(exc)


def test_resolve_ambiguous_url():
    reg = TargetRegistry()
    reg.register("t-a", "s-a", url="https://x/same")
    reg.register("t-b", "s-b", url="https://x/same-path")
    with pytest.raises(KeyError, match="ambiguous"):
        reg.resolve("same")


def test_resolve_index_out_of_range():
    reg = _registry()
    with pytest.raises(KeyError, match="out of range"):
        reg.resolve(7)


def test_primary_sticks_to_first_and_unregister_falls_back():
    reg = _registry()
    assert reg.primary_target_id == "t-main"
    reg.set_primary("t-popup")
    assert reg.primary_target_id == "t-popup"
    reg.unregister_target("t-popup")
    assert reg.primary_target_id == "t-main"
    assert reg.session_for_target("t-popup") is None


@pytest.mark.asyncio
async def test_attach_event_registers_without_hijacking_primary():
    """The slice-2 proven hijack: attachedToTarget must not replace _session_id."""
    conn = CDPConnection()
    conn._session_id = "sess-old"
    conn._target_id = "t-old"
    conn.targets.register("t-old", "sess-old", type="page", url="https://x/main")
    conn._post_attach_enable = AsyncMock()

    message = json.dumps(
        {
            "method": "Target.attachedToTarget",
            "params": {
                "sessionId": "sess-new",
                "targetInfo": {
                    "targetId": "t-new",
                    "type": "page",
                    "url": "https://x/popup",
                },
            },
        }
    )

    async def _messages():
        yield message

    ws = MagicMock()
    ws.__aiter__ = lambda self=None: _messages()
    conn._ws = ws

    await conn._recv_loop()

    assert conn._session_id == "sess-old"
    assert conn.targets.session_for_target("t-new") == "sess-new"
    assert conn.targets.primary_target_id == "t-old"


@pytest.mark.asyncio
async def test_switch_session_is_the_only_mutation_path():
    conn = CDPConnection()
    conn.targets.register("t-old", "sess-old", type="page")
    conn.targets.register("t-new", "sess-new", type="page")
    conn._session_id = "sess-old"

    conn.switch_session("sess-new")

    assert conn._session_id == "sess-new"
    assert conn.targets.primary_target_id == "t-new"
    with pytest.raises(KeyError):
        conn.switch_session("sess-ghost")
