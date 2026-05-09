"""
Tests for API models, batch loader, and batch processing.
"""

import sys
from unittest.mock import MagicMock

# Mock heavy dependencies before importing project modules
sys.modules["litellm"] = MagicMock()
sys.modules["PIL"] = MagicMock()
sys.modules["PIL.Image"] = MagicMock()
sys.modules["aiohttp"] = MagicMock()
sys.modules["aiohttp.web"] = MagicMock()
sys.modules["selenium"] = MagicMock()
sys.modules["selenium.webdriver"] = MagicMock()
sys.modules["selenium.webdriver.chrome.options"] = MagicMock()

import json
import pytest
from pathlib import Path

from src.api.models import (
    RunRequest, RunStatusResponse,
    FlowConfig, DeployWebhookPayload, BatchRunResponse, BatchStatusResponse,
    BatchConfig, BatchConfigFlow,
)
from src.api.batch_loader import _parse_dict
from src.api.manager import RunManager


# ─── Model Validation Tests ───────────────────────────────────────────

class TestRunRequest:
    def test_run_request_minimal(self):
        req = RunRequest(goal="Test goal", url="https://example.com")
        assert req.model == "gpt-4o-mini"
        assert req.headless is True
        assert req.app_version is None

    def test_run_request_with_version(self):
        req = RunRequest(
            goal="Test goal",
            url="https://example.com",
            app_version="1.0.0",
            git_commit="abc123",
            deployed_at="2026-05-09T14:00:00Z",
        )
        assert req.app_version == "1.0.0"
        assert req.git_commit == "abc123"
        assert req.deployed_at == "2026-05-09T14:00:00Z"

    def test_run_request_invalid_run_id(self):
        with pytest.raises(Exception):  # pydantic validation
            RunRequest(run_id="invalid id with spaces!", goal="test", url="https://example.com")


class TestFlowConfig:
    def test_flow_config_basic(self):
        flow = FlowConfig(goal="Document login", path="/login")
        assert flow.goal == "Document login"
        assert flow.path == "/login"
        assert flow.run_id is None

    def test_flow_config_full(self):
        flow = FlowConfig(
            goal="Document signup",
            path="/signup",
            run_id="signup-flow",
            title="Signup Guide",
            language="pt-BR",
        )
        assert flow.run_id == "signup-flow"
        assert flow.language == "pt-BR"


class TestDeployWebhookPayload:
    def test_webhook_payload_basic(self):
        payload = DeployWebhookPayload(
            app_version="2.3.1",
            git_commit="abc1234",
            deployed_at="2026-05-09T14:00:00Z",
            base_url="https://app.example.com",
            flows=[
                FlowConfig(goal="Document login", path="/login", run_id="login"),
                FlowConfig(goal="Document onboarding", path="/welcome"),
            ],
        )
        assert payload.app_version == "2.3.1"
        assert len(payload.flows) == 2
        assert payload.model == "gpt-4o-mini"
        assert payload.headless is True

    def test_webhook_payload_no_flows(self):
        """Empty flows is allowed by model but semantically invalid — tested at runtime."""
        payload = DeployWebhookPayload(
            app_version="1.0.0",
            git_commit="abc123",
            deployed_at="2026-05-09T14:00:00Z",
            base_url="https://example.com",
            flows=[],
        )
        assert payload.flows == []


class TestBatchRunResponse:
    def test_batch_response(self):
        resp = BatchRunResponse(
            batch_id="batch-abc123",
            total_flows=3,
            accepted=2,
            rejected=1,
            run_ids=["run-1", "run-2"],
        )
        assert resp.status == "ACCEPTED"
        assert resp.batch_id == "batch-abc123"


class TestBatchConfig:
    def test_batch_config_defaults(self):
        config = BatchConfig(flows=[BatchConfigFlow(goal="Test", path="/test")])
        assert config.model == "gpt-4o-mini"
        assert config.headless is True
        assert config.base_url is None

    def test_batch_config_full(self):
        config = BatchConfig(
            app_version="1.0.0",
            git_commit="abc123",
            deployed_at="2026-05-09T14:00:00Z",
            base_url="https://app.example.com",
            model="gpt-4",
            headless=False,
            flows=[
                BatchConfigFlow(goal="Login", path="/login", run_id="login", title="Login"),
            ],
        )
        assert config.base_url == "https://app.example.com"
        assert config.model == "gpt-4"
        assert config.headless is False


# ─── Batch Loader Tests ───────────────────────────────────────────────

class TestBatchLoader:
    def test_parse_dict_basic(self):
        data = {
            "app_version": "1.0.0",
            "base_url": "https://app.example.com",
            "flows": [
                {"goal": "Login", "path": "/login"},
                {"goal": "Signup", "path": "/signup", "run_id": "signup"},
            ],
        }
        config = _parse_dict(data)
        assert config.app_version == "1.0.0"
        assert len(config.flows) == 2
        assert config.flows[0].goal == "Login"
        assert config.flows[1].run_id == "signup"

    def test_parse_dict_empty_flows(self):
        data = {"base_url": "https://example.com"}
        config = _parse_dict(data)
        assert config.flows == []
        assert config.model == "gpt-4o-mini"

    def test_parse_dict_missing_fields(self):
        """Missing fields should use defaults."""
        data = {
            "flows": [
                {"goal": "Test", "path": "/test"},
            ],
        }
        config = _parse_dict(data)
        assert config.app_version is None
        assert config.git_commit is None
        assert config.headless is True


# ─── RunManager Batch Tests ──────────────────────────────────────────

class TestRunManagerBatch:
    def test_batch_registration(self):
        """Test that batch metadata is stored correctly."""
        batch_id = "test-batch-1"
        batch_info = {
            "batch_id": batch_id,
            "total_flows": 2,
            "accepted": 2,
            "rejected": 0,
            "run_ids": ["run-1", "run-2"],
            "status": "ACCEPTED",
        }
        RunManager._batches[batch_id] = batch_info

        status = RunManager.get_batch_status(batch_id)
        assert status is not None
        assert status["batch_id"] == batch_id
        assert status["total_flows"] == 2

        # Cleanup
        del RunManager._batches[batch_id]

    def test_batch_status_nonexistent(self):
        status = RunManager.get_batch_status("nonexistent")
        assert status is None

    def test_batch_builds_urls(self):
        """Test URL building logic in batch processing."""
        config = BatchConfig(
            base_url="https://app.example.com",
            flows=[
                BatchConfigFlow(goal="Login", path="/login"),
                BatchConfigFlow(goal="Signup", path="https://other.com/signup"),
            ],
        )

        # Simulate URL resolution
        base_url = config.base_url or ""
        urls = []
        for flow in config.flows:
            url = flow.path if flow.path.startswith("http") else f"{base_url}{flow.path}"
            urls.append(url)

        assert urls == ["https://app.example.com/login", "https://other.com/signup"]
