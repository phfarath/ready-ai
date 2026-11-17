"""Transport protocol (REF-02): the substitutable seam behind CDPConnection.

Architecture contract (Flow -> run_flow -> AgenticLoop -> executor -> CDP):
domains and agent helpers receive a ``Transport``, never the concrete
``CDPConnection``. Structural typing (``Protocol``) keeps this zero-cost at
runtime and leaves the door open for a BiDi transport later.

Only the surface the upper layers actually use is exposed: command sending,
event subscription/waiting, target/session management, liveness and the
shared event queue (previously reached as the private ``conn._events``).
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .connection import CDPEventContext, CDPEventSubscription


@runtime_checkable
class Transport(Protocol):
    """Minimal surface a CDP transport must provide to the upper layers."""

    async def send(
        self,
        method: str,
        params: Optional[dict] = None,
        session_id: Optional[str] = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]: ...

    def subscribe_events(
        self,
        *,
        context: Optional[CDPEventContext] = None,
        event_name: Optional[str] = None,
        after_sequence: Optional[int] = None,
    ) -> CDPEventSubscription: ...

    def events_since(
        self,
        sequence: int,
        *,
        context: Optional[CDPEventContext] = None,
        event_name: Optional[str] = None,
    ) -> list[dict[str, Any]]: ...

    async def wait_for_event(
        self,
        event_name: str,
        timeout: float = 30.0,
        *,
        context: Optional[CDPEventContext] = None,
        after_sequence: Optional[int] = None,
    ) -> dict[str, Any]: ...

    async def ensure_targets(self) -> None: ...

    async def list_targets(self) -> list[dict[str, Any]]: ...

    async def attach_to_target(self, target_id: str) -> str: ...

    async def close_target(self, target_id: str) -> bool: ...

    def switch_session(self, session_id: str) -> str: ...

    @property
    def session_id(self) -> Optional[str]: ...

    @property
    def is_alive(self) -> bool: ...

    def event_cursor(self) -> int: ...

    @property
    def event_queue(self) -> asyncio.Queue: ...
