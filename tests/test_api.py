import pytest
from httpx import ASGITransport, AsyncClient

from src.api.manager import RunManager
from src.api.models import RunStatusResponse
from src.api.server import app


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
