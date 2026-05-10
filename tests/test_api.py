import pytest
from httpx import ASGITransport, AsyncClient

from src.api.manager import RunManager
from src.api.models import RunStatusResponse
from src.api.server import app, _rate_limiter


@pytest.fixture(autouse=True)
def clear_rate_limit_store():
    """Reset rate limiter before each test."""
    yield
    _rate_limiter._buckets.clear()


@pytest.mark.asyncio
async def test_read_main(monkeypatch):
    run_id = "test-run-id"
    status = RunStatusResponse(
        run_id=run_id,
        status="PLANNING",
        goal="test goal",
        url="http://example.com",
        executed_steps=0,
        total_planned_steps=0,
        last_known_url=None,
    )

    async def fake_start_run(req):
        assert req.goal == "test goal"
        assert req.url == "http://example.com"
        return run_id

    def fake_get_status(requested_run_id):
        assert requested_run_id == run_id
        return status

    monkeypatch.setattr(RunManager, "start_run", fake_start_run)
    monkeypatch.setattr(RunManager, "get_status", fake_get_status)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/runs", json={"goal": "test goal", "url": "http://example.com"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "PLANNING"

        response2 = await client.get(f"/runs/{run_id}")
        assert response2.status_code == 200
        assert response2.json()["status"] == "PLANNING"


@pytest.mark.asyncio
async def test_health_check():
    """GET /health should return service status."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "ready-ai"
        assert "version" in data
        assert "timestamp" in data


@pytest.mark.asyncio
async def test_readiness_check():
    """GET /ready should return readiness status."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert "checks" in data
        assert data["checks"]["output_dir"] == "ok"
        assert "timestamp" in data


@pytest.mark.asyncio
async def test_cors_preflight():
    """OPTIONS request should return CORS headers."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.options(
            "/runs",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers
        assert "access-control-allow-methods" in response.headers


@pytest.mark.asyncio
async def test_rate_limit_exceeded(monkeypatch):
    """After run tier limit (5), should return 429 with Retry-After header."""
    run_id = "rate-limit-test"
    status = RunStatusResponse(
        run_id=run_id,
        status="PLANNING",
        goal="test",
        url="http://example.com",
        executed_steps=0,
        total_planned_steps=0,
        last_known_url=None,
    )

    async def fake_start_run(req):
        return run_id

    def fake_get_status(requested_run_id):
        return status

    monkeypatch.setattr(RunManager, "start_run", fake_start_run)
    monkeypatch.setattr(RunManager, "get_status", fake_get_status)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Make run tier limit requests (5 for /runs)
        for _ in range(5):
            response = await client.post("/runs", json={"goal": "test", "url": "http://example.com"})
            assert response.status_code == 200
            # Check rate limit headers are present
            assert "X-RateLimit-Limit" in response.headers
            assert "X-RateLimit-Remaining" in response.headers

        # Next request should be rate limited
        response = await client.post("/runs", json={"goal": "test", "url": "http://example.com"})
        assert response.status_code == 429
        assert "rate limit exceeded" in response.json()["detail"].lower()
        assert "Retry-After" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert response.headers["X-RateLimit-Remaining"] == "0"


@pytest.mark.asyncio
async def test_health_not_rate_limited():
    """Health endpoints should not be rate limited."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Make many health requests (no rate limit)
        for _ in range(50):
            response = await client.get("/health")
            assert response.status_code == 200
