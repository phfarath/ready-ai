import pytest
from unittest.mock import AsyncMock
from src.cdp.connection import CDPConnection
from src.cdp.page import PageDomain


@pytest.mark.asyncio
async def test_navigate_file_scheme_raises():
    conn = CDPConnection()
    conn._ws = AsyncMock()
    conn.send = AsyncMock(return_value={})
    page = PageDomain(conn)
    with pytest.raises(ValueError, match="Invalid URL scheme"):
        await page.navigate("file:///etc/passwd")


@pytest.mark.asyncio
async def test_navigate_javascript_scheme_raises():
    conn = CDPConnection()
    conn._ws = AsyncMock()
    conn.send = AsyncMock(return_value={})
    page = PageDomain(conn)
    with pytest.raises(ValueError, match="Invalid URL scheme"):
        await page.navigate("javascript:alert(1)")


@pytest.mark.asyncio
async def test_navigate_data_scheme_raises():
    conn = CDPConnection()
    conn._ws = AsyncMock()
    conn.send = AsyncMock(return_value={})
    page = PageDomain(conn)
    with pytest.raises(ValueError, match="Invalid URL scheme"):
        await page.navigate("data:text/html,<h1>hello</h1>")


@pytest.mark.asyncio
async def test_navigate_http_scheme_passes():
    conn = CDPConnection()
    conn._ws = AsyncMock()
    conn.send = AsyncMock(return_value={})
    page = PageDomain(conn)
    # Should not raise; disable wait_for_load and wait_for_network to avoid extra calls
    await page.navigate("http://example.com", wait_for_load=False, wait_for_network=False)
    # Verify that Page.navigate was called
    conn.send.assert_called_once_with("Page.navigate", {"url": "http://example.com"})


@pytest.mark.asyncio
async def test_navigate_https_scheme_passes():
    conn = CDPConnection()
    conn._ws = AsyncMock()
    conn.send = AsyncMock(return_value={})
    page = PageDomain(conn)
    await page.navigate("https://example.com", wait_for_load=False, wait_for_network=False)
    conn.send.assert_called_once_with("Page.navigate", {"url": "https://example.com"})


@pytest.mark.asyncio
async def test_navigate_scheme_case_insensitive():
    conn = CDPConnection()
    conn._ws = AsyncMock()
    conn.send = AsyncMock(return_value={})
    page = PageDomain(conn)
    # Uppercase HTTP
    await page.navigate("HTTP://example.com", wait_for_load=False, wait_for_network=False)
    conn.send.assert_called_with("Page.navigate", {"url": "HTTP://example.com"})
    # Reset mock
    conn.send.reset_mock()
    # Mixed case
    await page.navigate("HtTpS://example.com", wait_for_load=False, wait_for_network=False)
    conn.send.assert_called_with("Page.navigate", {"url": "HtTpS://example.com"})
