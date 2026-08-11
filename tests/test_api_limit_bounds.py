"""Security tests: API limit/offset query parameter bounds.

Validates VAL-SEC-007: API list endpoints MUST enforce ``limit`` in
``[0, 200]`` and ``offset >= 0``. Out-of-range values MUST return
HTTP 422.

Without bounds, ``limit=999999999`` causes a full directory scan that
can exhaust memory and CPU (denial-of-service vector).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.server import app, _rate_limiter


@pytest.fixture(autouse=True)
def clear_rate_limit_store():
    """Reset rate limiter before and after each test to avoid cross-test interference."""
    _rate_limiter._buckets.clear()
    yield
    _rate_limiter._buckets.clear()


# ─── /runs ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_runs_limit_too_large_returns_422():
    """GET /runs?limit=999999999 must return 422 (DoS prevention)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/runs", params={"limit": 999999999})
        assert resp.status_code == 422, (
            f"Expected 422 for limit=999999999, got {resp.status_code}: {resp.text[:200]}"
        )


@pytest.mark.asyncio
async def test_runs_limit_201_returns_422():
    """GET /runs?limit=201 must return 422 (just over the cap)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/runs", params={"limit": 201})
        assert resp.status_code == 422, (
            f"Expected 422 for limit=201, got {resp.status_code}: {resp.text[:200]}"
        )


@pytest.mark.asyncio
async def test_runs_limit_200_accepted():
    """GET /runs?limit=200 must NOT return 422 (boundary value allowed)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/runs", params={"limit": 200})
        assert resp.status_code != 422, (
            f"Expected non-422 for limit=200, got {resp.status_code}: {resp.text[:200]}"
        )


@pytest.mark.asyncio
async def test_runs_limit_0_accepted():
    """GET /runs?limit=0 must NOT return 422 (zero is a valid lower bound)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/runs", params={"limit": 0})
        assert resp.status_code != 422, (
            f"Expected non-422 for limit=0, got {resp.status_code}: {resp.text[:200]}"
        )


@pytest.mark.asyncio
async def test_runs_negative_limit_returns_422():
    """GET /runs?limit=-1 must return 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/runs", params={"limit": -1})
        assert resp.status_code == 422, (
            f"Expected 422 for limit=-1, got {resp.status_code}: {resp.text[:200]}"
        )


@pytest.mark.asyncio
async def test_runs_negative_offset_returns_422():
    """GET /runs?offset=-1 must return 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/runs", params={"offset": -1})
        assert resp.status_code == 422, (
            f"Expected 422 for offset=-1, got {resp.status_code}: {resp.text[:200]}"
        )


@pytest.mark.asyncio
async def test_runs_default_limit_no_422():
    """GET /runs with no params must NOT return 422 (defaults applied)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/runs")
        assert resp.status_code != 422, (
            f"Expected non-422 for default params, got {resp.status_code}: {resp.text[:200]}"
        )


# ─── /doc-sets ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_docs_limit_too_large_returns_422():
    """GET /doc-sets?limit=999999999 must return 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/doc-sets", params={"limit": 999999999})
        assert resp.status_code == 422, (
            f"Expected 422 for limit=999999999, got {resp.status_code}: {resp.text[:200]}"
        )


@pytest.mark.asyncio
async def test_docs_negative_offset_returns_422():
    """GET /doc-sets?offset=-1 must return 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/doc-sets", params={"offset": -1})
        assert resp.status_code == 422, (
            f"Expected 422 for offset=-1, got {resp.status_code}: {resp.text[:200]}"
        )


@pytest.mark.asyncio
async def test_docs_limit_200_accepted():
    """GET /doc-sets?limit=200 must NOT return 422 (boundary value)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/doc-sets", params={"limit": 200})
        assert resp.status_code != 422, (
            f"Expected non-422 for limit=200, got {resp.status_code}: {resp.text[:200]}"
        )


# ─── /history ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_history_limit_too_large_returns_422():
    """GET /history?limit=999999999 must return 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/history", params={"limit": 999999999})
        assert resp.status_code == 422, (
            f"Expected 422 for limit=999999999, got {resp.status_code}: {resp.text[:200]}"
        )


@pytest.mark.asyncio
async def test_history_negative_offset_returns_422():
    """GET /history?offset=-1 must return 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/history", params={"offset": -1})
        assert resp.status_code == 422, (
            f"Expected 422 for offset=-1, got {resp.status_code}: {resp.text[:200]}"
        )


@pytest.mark.asyncio
async def test_history_limit_200_accepted():
    """GET /history?limit=200 must NOT return 422 (boundary value)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/history", params={"limit": 200})
        assert resp.status_code != 422, (
            f"Expected non-422 for limit=200, got {resp.status_code}: {resp.text[:200]}"
        )
