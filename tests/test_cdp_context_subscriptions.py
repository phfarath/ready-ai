"""T-2 regression tests for context-safe CDP event routing."""

import asyncio

import pytest

from src.cdp.connection import CDPConnection, CDPEventContext
from src.cdp.exceptions import WebSocketDisconnected


def _event(method: str, *, session: str, frame: str | None = None, **params):
    payload = {"method": method, "sessionId": session, "params": params}
    if frame:
        payload["params"]["frameId"] = frame
    return payload


class TestContextSubscriptions:
    @pytest.mark.asyncio
    async def test_matching_subscribers_each_receive_the_same_event(self):
        conn = CDPConnection()
        left = conn.subscribe_events(
            context=CDPEventContext(session_id="left"), event_name="Page.loadEventFired"
        )
        right = conn.subscribe_events(
            context=CDPEventContext(session_id="left"), event_name="Page.loadEventFired"
        )
        await conn._publish_event(_event("Page.loadEventFired", session="left", timestamp=1))
        assert (await left.wait()).get("params", {}).get("timestamp") == 1
        assert (await right.wait()).get("params", {}).get("timestamp") == 1
        left.close()
        right.close()

    @pytest.mark.asyncio
    async def test_session_and_frame_isolation(self):
        conn = CDPConnection()
        target = conn.subscribe_events(
            context=CDPEventContext(session_id="s-1", frame_id="frame-main")
        )
        await conn._publish_event(_event("Page.frameNavigated", session="s-2", frame="frame-main"))
        await conn._publish_event(_event("Page.frameNavigated", session="s-1", frame="frame-child"))
        await conn._publish_event(_event("Page.frameNavigated", session="s-1", frame="frame-main"))
        received = await target.wait(0.2)
        assert received["sessionId"] == "s-1"
        assert received["params"]["frameId"] == "frame-main"
        target.close()

    @pytest.mark.asyncio
    async def test_history_closes_subscribe_after_action_race(self):
        conn = CDPConnection()
        cursor = conn.event_cursor
        await conn._publish_event(_event("Network.responseReceived", session="s-1", response={"status": 201}))
        subscription = conn.subscribe_events(
            context=CDPEventContext(session_id="s-1"),
            event_name="Network.responseReceived",
            after_sequence=cursor,
        )
        assert (await subscription.wait(0.2))["params"]["response"]["status"] == 201
        subscription.close()

    @pytest.mark.asyncio
    async def test_history_redacts_response_headers_and_query_strings(self):
        conn = CDPConnection()
        cursor = conn.event_cursor
        await conn._publish_event(
            _event(
                "Network.responseReceived",
                session="s-1",
                response={
                    "status": 201,
                    "url": "https://app.test/save?token=secret",
                    "headers": {"Set-Cookie": "session=secret"},
                },
            )
        )
        history = conn.events_since(cursor, context=CDPEventContext(session_id="s-1"))
        assert history == [
            {
                "method": "Network.responseReceived",
                "sessionId": "s-1",
                "params": {"response": {"status": 201, "url": "https://app.test/save"}},
            }
        ]

    @pytest.mark.asyncio
    async def test_cancelled_wait_does_not_unregister_or_consume_event(self):
        conn = CDPConnection()
        subscription = conn.subscribe_events(context=CDPEventContext(session_id="s-1"))
        task = asyncio.create_task(subscription.wait(10.0))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await conn._publish_event(_event("Page.loadEventFired", session="s-1"))
        assert (await subscription.wait(0.2))["method"] == "Page.loadEventFired"
        subscription.close()

    @pytest.mark.asyncio
    async def test_wait_aborts_on_disconnect(self):
        conn = CDPConnection()
        subscription = conn.subscribe_events(context=CDPEventContext(session_id="s-1"))
        await conn._handle_disconnect(intentional=False)
        with pytest.raises(WebSocketDisconnected):
            await subscription.wait(0.1)
        subscription.close()
