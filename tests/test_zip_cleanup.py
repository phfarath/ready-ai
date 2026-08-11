"""Tests for ZIP cleanup after API FileResponse (VAL-ROB-012).

After ``GET /runs/{run_id}/output`` the transient zip file MUST be deleted
once the response has been served.  The source directory must remain intact
and a second request must still succeed.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.server import app

client = TestClient(app)


@pytest.fixture
def run_output_dir(tmp_path, monkeypatch):
    """Create a fake run output directory inside *tmp_path* and chdir there.

    ``server.get_run_output`` builds paths relative to the CWD
    (``./output/{run_id}``), so we chdir into an isolated temp dir to keep
    the test hermetic.
    """
    monkeypatch.chdir(tmp_path)
    run_id = "zip-cleanup-test"
    run_dir = Path("./output") / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "docs.md").write_text("# Test\n")
    screenshots = run_dir / "screenshots"
    screenshots.mkdir()
    (screenshots / "step_01.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return run_id


def test_output_zip_deleted_after_response(run_output_dir):
    """After GET /runs/{run_id}/output, the transient zip must not exist."""
    run_id = run_output_dir
    zip_path = Path(f"./output/{run_id}.zip")

    response = client.get(f"/runs/{run_id}/output")

    assert response.status_code == 200
    assert not zip_path.exists(), "zip file should be deleted after response"


def test_source_directory_intact_after_response(run_output_dir):
    """The source directory ./output/{run_id}/ must remain intact."""
    run_id = run_output_dir
    source_dir = Path(f"./output/{run_id}")

    response = client.get(f"/runs/{run_id}/output")

    assert response.status_code == 200
    assert source_dir.exists(), "source directory must not be deleted"
    assert (source_dir / "docs.md").exists()
    assert (source_dir / "screenshots" / "step_01.png").exists()


def test_second_request_still_works(run_output_dir):
    """A second request to the same endpoint must still succeed."""
    run_id = run_output_dir

    resp1 = client.get(f"/runs/{run_id}/output")
    assert resp1.status_code == 200

    resp2 = client.get(f"/runs/{run_id}/output")
    assert resp2.status_code == 200
    assert resp2.headers["content-type"] == "application/zip"
