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

    @pytest.mark.asyncio
    async def test_polling_success_sets_cache(self):
        """On a successful idle detection (polling path), the cache IS set."""
        conn = CDPConnection()
        conn._ws = AsyncMock()
        conn.send = AsyncMock(return_value={})

        page = PageDomain(conn)
        # No events in queue and no in_flight requests → idle detected
        # after idle_time seconds of silence.
        await page.wait_for_network_idle(timeout=2.0, idle_time=0.05)
        assert page._network_idle_cache is not None

    @pytest.mark.asyncio
    async def test_polling_timeout_does_not_set_cache(self):
        """On timeout (polling path), the cache must NOT be set.

        A requestWillBeSent event keeps in_flight non-empty so the
        polling loop exhausts its deadline and times out. The cache
        should remain None so a subsequent call within the TTL does
        not return a stale 'idle' result.
        """
        conn = CDPConnection()
        conn._ws = AsyncMock()
        conn.send = AsyncMock(return_value={})

        page = PageDomain(conn)
        # Queue a request that never completes so in_flight stays
        # non-empty and the deadline expires.
        await conn._events.put({
            "method": "Network.requestWillBeSent",
            "params": {"requestId": "req-stuck"},
        })
        await page.wait_for_network_idle(timeout=0.3, idle_time=0.05)
        assert page._network_idle_cache is None

    @pytest.mark.asyncio
    async def test_lifecycle_timeout_does_not_set_cache(self, monkeypatch):
        """On timeout (lifecycle path), the cache must NOT be set.

        READY_AI_USE_LIFECYCLE_EVENTS is enabled but networkIdle never
        arrives, so the lifecycle path falls through to polling. A
        pending request keeps the polling path from detecting idle, so
        the overall result is a timeout. The cache must remain None.
        """
        monkeypatch.setenv("READY_AI_USE_LIFECYCLE_EVENTS", "true")
        conn = CDPConnection()
        conn._ws = AsyncMock()
        conn.send = AsyncMock(return_value={})

        page = PageDomain(conn)
        # Queue a request that never completes. The lifecycle loop will
        # stash and re-queue it, the polling fallback picks it up, and
        # in_flight stays non-empty until the deadline.
        await conn._events.put({
            "method": "Network.requestWillBeSent",
            "params": {"requestId": "req-stuck"},
        })
        await page.wait_for_network_idle(timeout=0.3, idle_time=0.05)
        assert page._network_idle_cache is None
