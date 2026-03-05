from fastapi.testclient import TestClient
from src.api.server import app

# Create a test client using the FastAPI app
client = TestClient(app)

def test_read_main():
    response = client.post("/runs", json={"goal": "test goal", "url": "http://example.com"})
    assert response.status_code == 200
    assert response.json()["status"] == "PLANNING"
    run_id = response.json()["run_id"]
    
    response2 = client.get(f"/runs/{run_id}")
    assert response2.status_code == 200
    assert response2.json()["status"] == "PLANNING"
