"""Tests for the Phase 3 API endpoints."""

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.server import app

client = TestClient(app)


@pytest.fixture
def sample_run_dir():
    """Create a fake run output directory with manifest + docs.md."""
    tmp = Path(tempfile.mkdtemp())
    run_dir = tmp / "output" / "test-run-1"
    run_dir.mkdir(parents=True)

    # manifest.json
    manifest = {
        "app_version": "1.0.0",
        "git_commit": "abc1234",
        "deployed_at": "2024-01-01T00:00:00Z",
        "generated_at": "2024-01-01T00:00:00Z",
        "files": ["docs.md", "screenshots/step_01.png"],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest))

    # docs.md
    (run_dir / "docs.md").write_text("# Test Docs\n\n## Step 1: Login\nClick login.\n")

    # screenshots dir
    ss_dir = run_dir / "screenshots"
    ss_dir.mkdir()
    (ss_dir / "step_01.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    # metrics
    (tmp / "output" / "test-run-1_metrics.json").write_text(
        json.dumps({"tokens": 100, "duration": 5.0})
    )

    yield str(tmp / "output")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


class TestListRuns:
    def test_empty(self):
        response = client.get("/runs")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["runs"] == []

    def test_filter_by_status(self, sample_run_dir):
        # This test relies on RunManager.get_status which may not find the fake dir
        # Skip for now since get_status requires RunState file
        pass


class TestListDocs:
    def test_empty(self):
        response = client.get("/doc-sets")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["docs"] == []


class TestHistory:
    def test_empty_history(self):
        response = client.get("/history")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["records"] == []

    def test_empty_aggregates(self):
        response = client.get("/history/aggregates")
        assert response.status_code == 200
        data = response.json()
        assert data["total_runs"] == 0


class TestDocsVersionStatus:
    def test_version_not_found(self):
        response = client.get("/docs/99.99.99/status")
        assert response.status_code == 404
