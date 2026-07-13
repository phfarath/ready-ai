"""Regression tests for VAL-QUAL-002: get_event_loop replaced by
get_running_loop in coroutine contexts.

``asyncio.get_event_loop()`` is deprecated in Python 3.12+ when there is
no running loop, and the recommended replacement inside a running
coroutine is ``asyncio.get_running_loop()``.  Both ``connection.py`` and
``page.py`` call ``get_event_loop().time()`` or ``loop = get_event_loop()``
inside ``async def`` bodies — every one of those must use
``get_running_loop()`` instead.

These tests perform a static inspection of the source files (mirroring the
``rg "get_event_loop"`` evidence required by the validation contract) plus
a behavioural check that the methods still function correctly under a
running event loop.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

SRC = Path(__file__).parent.parent / "src"

FILES = [
    SRC / "cdp" / "connection.py",
    SRC / "cdp" / "page.py",
]


# ---------------------------------------------------------------------------
# Static inspection
# ---------------------------------------------------------------------------

def test_no_get_event_loop_in_connection_or_page():
    """Neither connection.py nor page.py may reference get_event_loop.

    This mirrors the validation contract evidence requirement:
    ``rg "get_event_loop" src/cdp/connection.py src/cdp/page.py`` must
    return zero matches.
    """
    for f in FILES:
        text = f.read_text(encoding="utf-8")
        assert "get_event_loop" not in text, (
            f"{f.name} still references the deprecated get_event_loop(); "
            f"use get_running_loop() inside coroutine contexts."
        )


def test_get_running_loop_used_in_connection_and_page():
    """Both files must use get_running_loop in their async bodies."""
    for f in FILES:
        text = f.read_text(encoding="utf-8")
        assert "get_running_loop" in text, (
            f"{f.name} must use asyncio.get_running_loop() inside coroutine "
            f"contexts."
        )


# ---------------------------------------------------------------------------
# Behavioural checks: the methods must still work under a running loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_uses_running_loop_future():
    """CDPConnection.send creates a future via the running loop and resolves it."""
    from src.cdp.connection import CDPConnection
    from src.cdp.connection_state import ConnectionState

    conn = CDPConnection()
    conn._ws = AsyncMock()
    conn._state = ConnectionState.HEALTHY

    # send() stores a future keyed by msg_id; resolve it so the call returns.
    async def fake_ws_send(_data):
        # Resolve the just-created future.
        for msg_id, fut in list(conn._pending.items()):
            if not fut.done():
                fut.set_result({"id": msg_id, "result": {"ok": True}})

    conn._ws.send = fake_ws_send

    result = await conn.send("Page.enable")
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_wait_for_event_uses_running_loop_time():
    """wait_for_event relies on get_running_loop().time() for its deadline."""
    from src.cdp.connection import CDPConnection
    from src.cdp.connection_state import ConnectionState

    conn = CDPConnection()
    conn._state = ConnectionState.HEALTHY
    # Queue the target event so wait_for_event returns immediately.
    await conn._events.put({"method": "Page.loadEventFired", "params": {}})

    params = await conn.wait_for_event("Page.loadEventFired", timeout=1.0)
    assert params == {}


@pytest.mark.asyncio
async def test_wait_for_selector_uses_running_loop_time():
    """PageDomain.wait_for_selector uses get_running_loop().time() for deadline."""
    from src.cdp.connection import CDPConnection
    from src.cdp.page import PageDomain

    conn = CDPConnection()
    conn._ws = AsyncMock()
    # First evaluate returns True so the selector is found immediately.
    conn.send = AsyncMock(return_value={"result": {"value": True}})

    page = PageDomain(conn)
    found = await page.wait_for_selector("#foo", timeout=1.0)
    assert found is True
