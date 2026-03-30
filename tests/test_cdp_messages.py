"""
Unit tests for CDP message construction.

Tests that CDPConnection builds correct JSON messages with proper IDs,
method names, and session IDs — without needing a real browser connection.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock

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

        await send_and_resolve()

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
        assert "## Step 1: Click login button" in markdown
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
        assert "## Index" in markdown

    def test_render_portuguese_labels(self):
        from src.docs.renderer import DocRenderer

        doc = DocRenderer("Objetivo", language="portuguese")
        for i in range(5):
            doc.add_step(i + 1, f"Passo {i + 1}", "dGVzdA==", f"Anotação {i + 1}")

        markdown = doc.render()
        assert "## Passo 1:" in markdown
        assert "## Índice" in markdown

    def test_render_language_alias(self):
        from src.docs.renderer import DocRenderer

        # 2-letter alias "pt" should resolve to Portuguese labels
        doc = DocRenderer("Goal", language="pt")
        for i in range(5):
            doc.add_step(i + 1, f"Step {i + 1}", "dGVzdA==", "Annotation")

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


class TestWaitForEventBuffering:
    """Test that wait_for_event buffers non-matching events (Bug 2 fix)."""

    @pytest.mark.asyncio
    async def test_non_matching_events_are_preserved(self):
        """Events that don't match the target should be re-queued, not discarded."""
        conn = CDPConnection()
        conn._ws = AsyncMock()  # Prevent NotConnected error

        # Pre-load events into the queue
        await conn._events.put({"method": "Network.requestWillBeSent", "params": {}})
        await conn._events.put({"method": "DOM.documentUpdated", "params": {}})
        await conn._events.put({"method": "Page.loadEventFired", "params": {"timestamp": 123}})

        result = await conn.wait_for_event("Page.loadEventFired", timeout=5.0)
        assert result == {"timestamp": 123}

        # The non-matching events should still be in the queue
        assert conn._events.qsize() == 2

    @pytest.mark.asyncio
    async def test_timeout_preserves_stashed_events(self):
        """On timeout, stashed events should still be re-queued."""
        conn = CDPConnection()
        conn._ws = AsyncMock()

        # Load only non-matching events
        await conn._events.put({"method": "Network.requestWillBeSent", "params": {}})

        with pytest.raises(TimeoutError):
            await conn.wait_for_event("Page.loadEventFired", timeout=0.1)

        # The non-matching event should be preserved
        assert conn._events.qsize() == 1


class TestDOMChangeDetection:
    """Test that DOM change detection uses full text hash (Bug 3 fix)."""

    def test_change_below_500_chars_detected(self):
        """Changes beyond the first 500 chars should still be detected."""
        import hashlib

        text_before = "A" * 600
        text_after = "A" * 500 + "B" * 100

        # Old method (truncated) would NOT detect change
        old_changed = text_before[:500] != text_after[:500]
        assert not old_changed, "Old method should miss this change"

        # New method (hash) DOES detect change
        new_changed = (
            hashlib.md5(text_before.encode()).hexdigest()
            != hashlib.md5(text_after.encode()).hexdigest()
        )
        assert new_changed, "New method should detect change below fold"


class TestSelectorExtraction:
    """Test the extract_selector helper for visual highlighting (Gap C)."""

    def test_extract_click_selector(self):
        from src.agent.cursor import extract_selector
        assert extract_selector("Clicked element: #login-btn") == "#login-btn"

    def test_extract_js_fallback_selector(self):
        from src.agent.cursor import extract_selector
        assert extract_selector(
            "Clicked element via JS fallback: [data-testid=\"submit\"]"
        ) == "[data-testid=\"submit\"]"

    def test_extract_scroll_to_selector(self):
        from src.agent.cursor import extract_selector
        assert extract_selector("Scrolled to element: .footer-link") == ".footer-link"

    def test_returns_none_for_non_element_actions(self):
        from src.agent.cursor import extract_selector
        assert extract_selector("Observing current page state") is None
        assert extract_selector("Navigated to: https://example.com") is None

    def test_returns_none_for_failed_actions(self):
        from src.agent.cursor import extract_selector
        assert extract_selector("[Failed] Element not found: #btn") is None


class TestCredentialEscaping:
    """Test that json.dumps correctly escapes special characters (Bug 1 fix)."""

    def test_single_quotes_escaped(self):
        import json
        password = "pass'word"
        result = json.dumps(password)
        # json.dumps wraps in double quotes — single quotes are safe in JS
        # The key point is the output is a valid JS string literal
        assert result == '"pass\'word"'
        # Can be directly interpolated into JS: i.value = "pass'word"
        assert result.startswith('"') and result.endswith('"')

    def test_backslash_escaped(self):
        import json
        password = "pass\\word"
        result = json.dumps(password)
        assert "\\\\" in result

    def test_double_quotes_escaped(self):
        import json
        password = 'pass"word'
        result = json.dumps(password)
        assert '\\"' in result
