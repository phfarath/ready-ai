"""
Tests for MCP Server.
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from src.mcp_server import (
    TOOLS,
    _handle_check_health,
    _handle_generate_docs,
    _handle_test_docs,
)


class TestMCPDefinitions:
    def test_tools_list_has_three_tools(self):
        assert len(TOOLS) == 3
        names = {t["name"] for t in TOOLS}
        assert "ready_ai_generate_docs" in names
        assert "ready_ai_test_docs" in names
        assert "ready_ai_check_health" in names

    def test_generate_docs_schema_requires_goal_and_url(self):
        tool = next(t for t in TOOLS if t["name"] == "ready_ai_generate_docs")
        required = tool["inputSchema"]["required"]
        assert "goal" in required
        assert "url" in required

    def test_test_docs_schema_requires_doc_path_and_url(self):
        tool = next(t for t in TOOLS if t["name"] == "ready_ai_test_docs")
        required = tool["inputSchema"]["required"]
        assert "doc_path" in required
        assert "url" in required

    def test_check_health_has_no_required_params(self):
        tool = next(t for t in TOOLS if t["name"] == "ready_ai_check_health")
        assert not tool["inputSchema"].get("required")


class TestHandleCheckHealth:
    @pytest.mark.asyncio
    async def test_returns_healthy_status(self):
        result = await _handle_check_health({})
        assert result["isError"] is False
        assert "ready-ai is healthy" in result["content"][0]["text"]
        assert "generate_docs" in result["content"][0]["text"]
        assert "test_docs" in result["content"][0]["text"]


class TestHandleGenerateDocs:
    @pytest.mark.asyncio
    async def test_successful_run(self, monkeypatch):
        async def fake_run(self):
            return "./output/test-run/docs.md"

        monkeypatch.setattr("src.agent.loop.AgenticLoop.run", fake_run)

        result = await _handle_generate_docs({
            "goal": "Test flow",
            "url": "https://example.com",
            "language": "en",
        })

        assert result["isError"] is False
        assert "Documentation generated" in result["content"][0]["text"]
        assert "test-run" in result["content"][0]["text"]
        assert "Test flow" in result["content"][0]["text"]  # goal

    @pytest.mark.asyncio
    async def test_failed_run(self, monkeypatch):
        async def fake_run(self):
            raise RuntimeError("Browser not available")

        monkeypatch.setattr("src.agent.loop.AgenticLoop.run", fake_run)

        result = await _handle_generate_docs({
            "goal": "Test flow",
            "url": "https://example.com",
        })

        assert result["isError"] is True
        assert "Browser not available" in result["content"][0]["text"]


class TestHandleTestDocs:
    @pytest.mark.asyncio
    async def test_passed_status(self, monkeypatch):
        mock_report = MagicMock()
        mock_report.overall_status = "PASSED"
        mock_report.results = [{}, {}, {}]
        mock_report.steps_outdated = []
        mock_report.steps_broken = []

        async def fake_run(self):
            return mock_report

        monkeypatch.setattr("src.agent.test_runner.DocTestRunner.run", fake_run)

        result = await _handle_test_docs({
            "doc_path": "./docs.md",
            "url": "https://example.com",
        })

        assert result["isError"] is False
        assert "PASSED" in result["content"][0]["text"]
        assert "3" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_broken_status(self, monkeypatch):
        mock_report = MagicMock()
        mock_report.overall_status = "BROKEN"
        mock_report.results = [{}, {}]
        mock_report.steps_outdated = [1]
        mock_report.steps_broken = [2]

        async def fake_run(self):
            return mock_report

        monkeypatch.setattr("src.agent.test_runner.DocTestRunner.run", fake_run)

        result = await _handle_test_docs({
            "doc_path": "./docs.md",
            "url": "https://example.com",
        })

        assert result["isError"] is True
        assert "BROKEN" in result["content"][0]["text"]
