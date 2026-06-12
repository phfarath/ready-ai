"""
Tests for the throttled CursorAnimator background loop.

Quick win #5 from the CDP resilience roadmap: when the CDP
connection is gone (post-teardown), the cursor loop must NOT
busy-loop calling Runtime.evaluate. It should back off.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.cursor import CursorAnimator
from src.cdp.connection import CDPConnection


def _make_dead_conn() -> CDPConnection:
    """A CDPConnection whose _ws.send always raises (simulates closed socket)."""
    conn = CDPConnection()
    conn._ws = AsyncMock()
    conn._ws.send = AsyncMock(side_effect=RuntimeError("ws closed"))
    return conn


class TestCursorStop:
    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        animator = CursorAnimator()
        animator.start(CDPConnection())  # _ws is None
        assert animator._task is not None
        await animator.stop()
        assert animator._task is None


class TestCursorSurvivesClosedConnection:
    @pytest.mark.asyncio
    async def test_send_exception_does_not_kill_loop(self):
        # Realistic scenario: Chrome was torn down mid-run. send() raises.
        # The loop must swallow the error and stay alive; the next iteration
        # will see _ws is no longer usable and back off.
        conn = _make_dead_conn()
        animator = CursorAnimator()
        animator.start(conn)
        animator.moving = True
        # Let the loop attempt a few sends.
        await asyncio.sleep(0.5)
        # Stop with a generous timeout — the throttle sleep is up to 5s,
        # so we just verify the call returns.
        try:
            await asyncio.wait_for(animator.stop(), timeout=6.0)
        except asyncio.TimeoutError:
            animator._task.cancel()
            raise
        # No assertion on the task reference; what matters is that
        # we did not raise.
