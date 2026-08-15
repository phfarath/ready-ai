"""T-2 deterministic page waits and passive-evidence regression tests."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.cdp.connection import CDPConnection, CDPEventContext
from src.cdp.page import PageDomain


@pytest.mark.asyncio
async def test_network_http_evidence_is_sanitized_and_scoped():
    conn = CDPConnection()
    page = PageDomain(conn, CDPEventContext(session_id="page-1"))
    cursor = page.event_cursor
    await conn._publish_event(
        {
            "method": "Network.responseReceived",
            "sessionId": "other",
            "params": {"response": {"status": 500, "url": "https://bad.test/api?token=secret"}},
        }
    )
    await conn._publish_event(
        {
            "method": "Network.responseReceived",
            "sessionId": "page-1",
            "params": {"response": {"status": 422, "url": "https://app.test/api/save?token=secret"}},
        }
    )
    evidence = page.http_failures_since(cursor)
    assert len(evidence) == 1
    assert evidence[0].details == {"status": 422, "url": "https://app.test/api/save"}


@pytest.mark.asyncio
async def test_wait_for_http_uses_passive_subscription():
    conn = CDPConnection()
    page = PageDomain(conn, CDPEventContext(session_id="page-1"))
    cursor = page.event_cursor
    waiter = asyncio.create_task(page.wait_for_http(status=201, after_sequence=cursor, timeout=1.0))
    await asyncio.sleep(0)
    await conn._publish_event(
        {
            "method": "Network.responseReceived",
            "sessionId": "page-1",
            "params": {"response": {"status": 201, "url": "https://app.test/create?csrf=nope"}},
        }
    )
    evidence = await waiter
    assert evidence is not None
    assert evidence.passed is True
    assert evidence.details["url"] == "https://app.test/create"


@pytest.mark.asyncio
async def test_wait_for_download_skips_nonmatching_filename():
    conn = CDPConnection()
    page = PageDomain(conn, CDPEventContext(session_id="page-1"))
    cursor = page.event_cursor
    waiter = asyncio.create_task(
        page.wait_for_download(filename="report.csv", after_sequence=cursor, timeout=1.0)
    )
    await asyncio.sleep(0)
    for suggested in ("other.csv", "report.csv"):
        await conn._publish_event(
            {
                "method": "Page.downloadWillBegin",
                "sessionId": "page-1",
                "params": {"suggestedFilename": suggested, "url": "https://app.test/export?token=nope"},
            }
        )
    evidence = await waiter
    assert evidence is not None
    assert evidence.details == {"filename": "report.csv", "url": "https://app.test/export"}


@pytest.mark.asyncio
async def test_wait_for_element_visible_enabled_and_stable():
    conn = CDPConnection()
    conn.send = AsyncMock(
        return_value={
            "result": {
                "value": {"visible": True, "enabled": True, "rect": [1, 2, 30, 20]}
            }
        }
    )
    page = PageDomain(conn)
    assert await page.wait_for_element("#save", state="visible", timeout=0.1)
    assert await page.wait_for_element("#save", state="enabled", timeout=0.1)
    assert await page.wait_for_element("#save", state="stable", timeout=0.3, stable_for=0.02)


@pytest.mark.asyncio
async def test_wait_for_text_and_url_are_bounded():
    conn = CDPConnection()
    values = iter([False, True, "https://app.test/done"])
    conn.send = AsyncMock(side_effect=lambda *args, **kwargs: {"result": {"value": next(values)}})
    page = PageDomain(conn)
    assert await page.wait_for_text("Saved", timeout=0.2)
    assert await page.wait_for_url("/done", timeout=0.2)
