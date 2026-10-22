"""REF-02: a camada CDP fica atras de um protocolo Transport substituivel.

Contrato maquina-legivel da arquitetura (Flow -> run_flow -> AgenticLoop ->
executor -> CDP): dominios recebem Transport, nunca CDPConnection concreto;
page.py nao alcanca a fila privada `conn._events`.
"""
from __future__ import annotations

import asyncio
import typing
from pathlib import Path


def _hints_of(fn):
    return typing.get_type_hints(fn)


def test_transport_protocol_exists_with_expected_members():
    from src.cdp.transport import Transport

    for member in (
        "send",
        "subscribe_events",
        "events_since",
        "wait_for_event",
        "ensure_targets",
        "list_targets",
        "attach_to_target",
        "close_target",
        "switch_session",
        "session_id",
        "is_alive",
        "event_cursor",
        "event_queue",
    ):
        assert hasattr(Transport, member), member
    assert getattr(Transport, "_is_runtime_protocol", False) is True


def test_connection_satisfies_transport():
    from src.cdp.connection import CDPConnection
    from src.cdp.transport import Transport

    assert isinstance(CDPConnection(), Transport)


def test_domains_receive_transport_not_concrete_connection():
    from src.agent.cursor import CursorAnimator
    from src.cdp.accessibility import AccessibilityDomain, get_ax_snapshot
    from src.cdp.input import InputDomain
    from src.cdp.locator import ActionValidator, Locator
    from src.cdp.page import PageDomain
    from src.cdp.runtime import RuntimeDomain
    from src.cdp.transport import Transport

    for fn in (
        PageDomain.__init__,
        RuntimeDomain.__init__,
        InputDomain.__init__,
        AccessibilityDomain.__init__,
        Locator.__init__,
        ActionValidator.__init__,
        CursorAnimator.start,
        get_ax_snapshot,
    ):
        hints = _hints_of(fn)
        assert hints.get("conn") is Transport, fn


def test_stub_transport_is_accepted_where_connection_was():
    from src.cdp.transport import Transport

    class StubTransport:
        async def send(self, method, params=None, session_id=None, timeout=30.0):
            return {}

        def subscribe_events(self, **kwargs):
            raise NotImplementedError

        def events_since(self, sequence, **kwargs):
            return []

        async def wait_for_event(self, event_name, timeout=30.0, **kwargs):
            return {}

        async def ensure_targets(self):
            return None

        async def list_targets(self):
            return []

        async def attach_to_target(self, target_id):
            return "session-stub"

        async def close_target(self, target_id):
            return True

        def switch_session(self, session_id):
            return "target-stub"

        @property
        def session_id(self):
            return "session-stub"

        @property
        def is_alive(self):
            return True

        def event_cursor(self):
            return 0

        @property
        def event_queue(self):
            return asyncio.Queue()

    assert isinstance(StubTransport(), Transport)


def test_domains_never_reach_private_event_queue():
    src = Path("src/cdp/page.py").read_text(encoding="utf-8")
    assert "conn._events" not in src
