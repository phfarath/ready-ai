"""
Unit tests for CDP message construction.

Tests that CDPConnection builds correct JSON messages with proper IDs,
method names, and session IDs — without needing a real browser connection.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cdp.connection import CDPConnection


class TestCDPConnection:
    """Test CDP message ID tracking and construction."""

    def test_next_id_increments(self):
        conn = CDPConnection()
        assert conn._next_id() == 1
        assert conn._next_id() == 2
        assert conn._next_id() == 3

    def test_initial_state(self):
        conn = CDPConnection()
        assert conn._ws is None
        assert conn._session_id is None
        assert conn._msg_id == 0
        assert len(conn._pending) == 0

    @pytest.mark.asyncio
    async def test_send_builds_correct_message(self):
        """Verify the JSON message structure sent over WebSocket."""
        conn = CDPConnection()

        # Mock WebSocket
        mock_ws = AsyncMock()
        conn._ws = mock_ws

        # Create a future that resolves with a mock response
        loop = asyncio.get_event_loop()
        sent_messages = []

        async def capture_send(data):
            sent_messages.append(json.loads(data))

        mock_ws.send = capture_send

        # Start send (will wait for response, so we need to resolve the future)
        async def send_and_resolve():
            # Schedule the send
            task = asyncio.create_task(
                conn.send("Page.navigate", {"url": "https://example.com"})
            )
            # Give it time to register the pending future
            await asyncio.sleep(0.01)

            # Resolve the pending future
            if 1 in conn._pending:
                conn._pending[1].set_result({"id": 1, "result": {"frameId": "123"}})

            return await task

        result = await send_and_resolve()

        # Verify sent message
        assert len(sent_messages) == 1
        msg = sent_messages[0]
        assert msg["id"] == 1
        assert msg["method"] == "Page.navigate"
        assert msg["params"] == {"url": "https://example.com"}
        assert "sessionId" not in msg

    @pytest.mark.asyncio
    async def test_send_includes_session_id(self):
        """Verify session ID is included when set."""
        conn = CDPConnection()
        conn._session_id = "test-session-123"

        mock_ws = AsyncMock()
        conn._ws = mock_ws

        sent_messages = []

        async def capture_send(data):
            sent_messages.append(json.loads(data))

        mock_ws.send = capture_send

        async def send_and_resolve():
            task = asyncio.create_task(conn.send("DOM.getDocument"))
            await asyncio.sleep(0.01)
            if 1 in conn._pending:
                conn._pending[1].set_result({"id": 1, "result": {}})
            return await task

        await send_and_resolve()

        msg = sent_messages[0]
        assert msg["sessionId"] == "test-session-123"

    @pytest.mark.asyncio
    async def test_send_raises_on_not_connected(self):
        """Verify RuntimeError when not connected."""
        conn = CDPConnection()
        with pytest.raises(RuntimeError, match="Not connected"):
            await conn.send("Page.navigate")

    @pytest.mark.asyncio
    async def test_send_raises_on_cdp_error(self):
        """Verify RuntimeError on CDP error responses."""
        conn = CDPConnection()
        mock_ws = AsyncMock()
        conn._ws = mock_ws
        mock_ws.send = AsyncMock()

        async def send_and_error():
            task = asyncio.create_task(conn.send("Bad.method"))
            await asyncio.sleep(0.01)
            if 1 in conn._pending:
                conn._pending[1].set_result({
                    "id": 1,
                    "error": {"code": -32601, "message": "Method not found"}
                })
            return await task

        with pytest.raises(RuntimeError, match="Method not found"):
            await send_and_error()


class TestDocRenderer:
    """Test the markdown documentation renderer."""

    def test_render_basic(self):
        from src.docs.renderer import DocRenderer

        doc = DocRenderer("Test Goal")
        doc.add_step(
            step_number=1,
            title="Click login button",
            screenshot_b64="dGVzdA==",  # "test" in base64
            annotation="This shows the login page with the button highlighted.",
            action_description="Clicked element: #login-btn",
        )

        markdown = doc.render()

        assert "# Test Goal" in markdown
        assert "## Passo 1: Click login button" in markdown
        assert "screenshots/step_01.png" in markdown
        assert "login page" in markdown
        assert "Clicked element: #login-btn" in markdown

    def test_render_multiple_steps_has_toc(self):
        from src.docs.renderer import DocRenderer

        doc = DocRenderer("Multi Step Test")
        for i in range(5):
            doc.add_step(
                step_number=i + 1,
                title=f"Step {i + 1}",
                screenshot_b64="dGVzdA==",
                annotation=f"Annotation {i + 1}",
            )

        markdown = doc.render()
        assert "## Índice" in markdown

    def test_screenshots_dict(self):
        from src.docs.renderer import DocRenderer

        doc = DocRenderer("Test")
        doc.add_step(1, "Step 1", "abc123", "Annotation")

        assert "step_01.png" in doc.screenshots
        assert doc.screenshots["step_01.png"] == "abc123"


class TestPlannerParse:
    """Test the planner's step parser."""

    def test_parse_numbered_steps(self):
        from src.agent.planner import _parse_steps

        response = """1. Click the login button
2. Enter email address
3. Click submit"""

        steps = _parse_steps(response)
        assert len(steps) == 3
        assert steps[0] == "Click the login button"
        assert steps[1] == "Enter email address"
        assert steps[2] == "Click submit"

    def test_parse_parenthesis_numbering(self):
        from src.agent.planner import _parse_steps

        response = """1) First step
2) Second step"""

        steps = _parse_steps(response)
        assert len(steps) == 2

    def test_parse_dash_list(self):
        from src.agent.planner import _parse_steps

        response = """- Step one
- Step two"""

        steps = _parse_steps(response)
        assert len(steps) == 2

    def test_parse_empty(self):
        from src.agent.planner import _parse_steps

        assert _parse_steps("") == []
        assert _parse_steps("\n\n") == []


class TestExecutorParse:
    """Test the executor's action parser."""

    def test_parse_json_action(self):
        from src.agent.executor import _parse_action

        result = _parse_action('{"action": "click", "selector": "#btn"}')
        assert result == {"action": "click", "selector": "#btn"}

    def test_parse_json_in_code_block(self):
        from src.agent.executor import _parse_action

        result = _parse_action('```json\n{"action": "type", "text": "hello"}\n```')
        assert result["action"] == "type"
        assert result["text"] == "hello"

    def test_parse_invalid_returns_none(self):
        from src.agent.executor import _parse_action

        assert _parse_action("not json at all") is None
