"""
Tests for the PageDomain.wait_for_network_idle short-lived cache.

Quick win #6: back-to-back calls within a 1s TTL should not
repeat the event-loop scan; calls after the TTL should.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cdp.connection import CDPConnection
from src.cdp.page import PageDomain


class TestNetworkIdleCache:
    @pytest.mark.asyncio
    async def test_first_call_enables_network(self):
        conn = CDPConnection()
        conn._ws = AsyncMock()
        conn.send = AsyncMock(return_value={})

        page = PageDomain(conn)
        # Pre-load one no-op event so the loop can hit the "no event
        # for idle_time" branch and exit on its first iteration.
        # Without an event arriving within idle_time, the loop sees
        # no in_flight requests and breaks.
        await page.wait_for_network_idle(timeout=2.0, idle_time=0.05)
        # Network.enable was called exactly once.
        assert conn.send.await_count == 1
        assert conn.send.await_args.args[0] == "Network.enable"

    @pytest.mark.asyncio
    async def test_cache_hit_within_ttl(self):
        conn = CDPConnection()
        conn._ws = AsyncMock()
        conn.send = AsyncMock(return_value={})

        page = PageDomain(conn)
        await page.wait_for_network_idle(timeout=2.0, idle_time=0.05)
        first_call_count = conn.send.await_count

        # Second call within the 1s TTL must not re-issue Network.enable.
        await page.wait_for_network_idle(timeout=2.0, idle_time=0.05)
        assert conn.send.await_count == first_call_count

    @pytest.mark.asyncio
    async def test_cache_miss_after_ttl(self):
        conn = CDPConnection()
        conn._ws = AsyncMock()
        conn.send = AsyncMock(return_value={})

        page = PageDomain(conn)
        # Force the TTL to expire instantly.
        page._network_idle_cache_ttl_s = 0.0
        await page.wait_for_network_idle(timeout=2.0, idle_time=0.05)
        await page.wait_for_network_idle(timeout=2.0, idle_time=0.05)
        # Both calls should have hit the network.
        assert conn.send.await_count == 2

    @pytest.mark.asyncio
    async def test_cache_keyed_per_instance(self):
        conn = CDPConnection()
        conn._ws = AsyncMock()
        conn.send = AsyncMock(return_value={})

        page_a = PageDomain(conn)
        page_b = PageDomain(conn)
        await page_a.wait_for_network_idle(timeout=2.0, idle_time=0.05)
        # A different PageDomain instance must NOT share the cache.
        await page_b.wait_for_network_idle(timeout=2.0, idle_time=0.05)
        assert conn.send.await_count == 2
