"""Tests for the manifest module (src/docs/manifest.py)."""

from pathlib import Path
from src.docs.manifest import DocManifest, create_manifest


class TestDocManifest:
    def test_to_dict(self):
        manifest = DocManifest(
            run_id="test-run-1",
            goal="Document login",
            url="https://app.example.com",
            app_version="2.3.1",
            git_commit="abc1234",
            deployed_at="2026-05-09T14:00:00Z",
            language="en",
            steps_count=5,
            screenshots_count=5,
            status="FINISHED",
            model="gpt-4o-mini",
        )

        d = manifest.to_dict()
        assert d["run_id"] == "test-run-1"
        assert d["goal"] == "Document login"
        assert d["url"] == "https://app.example.com"
        assert d["app_version"] == "2.3.1"
        assert d["git_commit"] == "abc1234"
        assert d["deployed_at"] == "2026-05-09T14:00:00Z"
        assert d["steps_count"] == 5
        assert d["screenshots_count"] == 5
        assert d["status"] == "FINISHED"
        assert d["model"] == "gpt-4o-mini"

    def test_to_file_and_from_file(self, tmp_path: Path):
        manifest = DocManifest(
            run_id="test-run-1",
            goal="Document login",
            url="https://app.example.com",
            app_version="2.3.1",
        )

        path = tmp_path / "manifest.json"
        manifest.to_file(path)

        assert path.exists()
        loaded = DocManifest.from_file(path)
        assert loaded is not None
        assert loaded.run_id == "test-run-1"
        assert loaded.app_version == "2.3.1"

    def test_from_file_nonexistent(self, tmp_path: Path):
        loaded = DocManifest.from_file(tmp_path / "nonexistent.json")
        assert loaded is None


class TestCreateManifest:
    def test_explicit_values(self):
        manifest = create_manifest(
            run_id="run-1",
            goal="Document login",
            url="https://app.example.com",
            app_version="2.3.1",
            git_commit="abc1234",
            deployed_at="2026-05-09T14:00:00Z",
            steps_count=3,
            status="FINISHED",
            model="gpt-4o-mini",
        )

        assert manifest.app_version == "2.3.1"
        assert manifest.git_commit == "abc1234"
        assert manifest.deployed_at == "2026-05-09T14:00:00Z"
        assert manifest.steps_count == 3
        assert manifest.status == "FINISHED"
        assert manifest.generated_at  # auto-set

    def test_auto_resolution(self, monkeypatch):
        monkeypatch.setenv("APP_VERSION", "1.0.0")
        monkeypatch.setenv("GITHUB_SHA", "1234567890abcdef1234567890abcdef12345678")
        monkeypatch.setenv("DEPLOYED_AT", "2026-01-01T00:00:00Z")

        manifest = create_manifest(
            run_id="run-1",
            goal="Document login",
            url="https://app.example.com",
        )

        assert manifest.app_version == "1.0.0"
        assert manifest.git_commit == "1234567890abcdef1234567890abcdef12345678"
        assert manifest.deployed_at == "2026-01-01T00:00:00Z"
