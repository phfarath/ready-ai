import pytest
from httpx import ASGITransport, AsyncClient

from src.api.manager import RunManager
from src.api.server import app


@pytest.mark.asyncio
async def test_create_run_returns_conflict_for_active_run(monkeypatch):
    async def fake_start_run(_req):
        raise ValueError("Run 'duplicate-run' is already active.")

    monkeypatch.setattr(RunManager, "start_run", fake_start_run)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/runs", json={"goal": "Test API", "url": "http://example.com"}
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "Run 'duplicate-run' is already active."


@pytest.mark.asyncio
async def test_get_run_output_returns_404_when_output_is_missing():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/runs/missing-run/output")

    assert response.status_code == 404
    assert response.json()["detail"] == "Output directory not found."
