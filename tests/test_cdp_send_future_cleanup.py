"""
Unit tests for CDPConnection.send() future cleanup on _ws.send failure.

Tests VAL-ROB-006: When self._ws.send raises in CDPConnection.send(),
the future MUST be removed from self._pending. The exception MUST
propagate to the caller.
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cdp.connection import CDPConnection


class TestSendFutureCleanup:
    """Verify that send() cleans up _pending when _ws.send raises."""

    @pytest.mark.asyncio
    async def test_future_popped_on_ws_send_failure(self):
        """When _ws.send raises, msg_id must not remain in _pending."""
        conn = CDPConnection()

        # Mock WebSocket whose .send raises
        mock_ws = AsyncMock()

        async def failing_send(_data):
            raise ConnectionError("WebSocket closed during send")

        mock_ws.send = failing_send
        conn._ws = mock_ws

        # The exception should propagate to the caller
        with pytest.raises(ConnectionError, match="WebSocket closed"):
            await conn.send("Page.navigate", {"url": "https://example.com"})

        # msg_id 1 should have been popped from _pending
        assert 1 not in conn._pending, (
            "Future leaked: msg_id still in _pending after _ws.send raised"
        )

    @pytest.mark.asyncio
    async def test_exception_propagates_on_ws_send_failure(self):
        """The original exception must propagate, not be swallowed."""
        conn = CDPConnection()

        mock_ws = AsyncMock()

        async def failing_send(_data):
            raise OSError("Network unreachable")

        mock_ws.send = failing_send
        conn._ws = mock_ws

        with pytest.raises(OSError, match="Network unreachable"):
            await conn.send("Runtime.evaluate")

    @pytest.mark.asyncio
    async def test_pending_empty_after_send_failure(self):
        """_pending dict should be empty after a failed send."""
        conn = CDPConnection()

        mock_ws = AsyncMock()

        async def failing_send(_data):
            raise RuntimeError("send failed")

        mock_ws.send = failing_send
        conn._ws = mock_ws

        with pytest.raises(RuntimeError):
            await conn.send("Page.enable")

        assert len(conn._pending) == 0, (
            "_pending should be empty after send failure"
        )

    @pytest.mark.asyncio
    async def test_success_does_not_leave_orphan(self):
        """A successful send should work normally (no regression)."""
        conn = CDPConnection()

        sent_messages = []
        mock_ws = AsyncMock()

        async def capture_send(data):
            sent_messages.append(json.loads(data))

        mock_ws.send = capture_send
        conn._ws = mock_ws

        async def send_and_resolve():
            task = asyncio.create_task(conn.send("Page.enable"))
            await asyncio.sleep(0.01)
            if 1 in conn._pending:
                conn._pending[1].set_result({"id": 1, "result": {}})
            return await task

        result = await send_and_resolve()
        assert result == {}
        # After success, the response handler (or the caller here) removes it
        # The key point is no future is stuck waiting forever
