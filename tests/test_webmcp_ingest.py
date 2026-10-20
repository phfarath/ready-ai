"""US8 WebMCP opportunistic ingestion — TDD RED.

Detecta ``navigator.modelContext`` via Runtime.evaluate e injeta os
schemas como contexto do planner, atrás de flag (default off).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent import planner
from src.cdp.runtime import RuntimeDomain


def _runtime_with(evaluate_result) -> RuntimeDomain:
    runtime = RuntimeDomain(conn=MagicMock())
    runtime.evaluate = AsyncMock(return_value=evaluate_result)
    return runtime


async def test_detect_absent_returns_empty():
    runtime = _runtime_with(None)
    assert await runtime.detect_model_context() == []


async def test_detect_present_returns_tools():
    runtime = _runtime_with(
        {
            "present": True,
            "tools": [
                {
                    "name": "shop.search",
                    "description": "Search the catalog",
                    "input": {"type": "object"},
                }
            ],
        }
    )
    tools = await runtime.detect_model_context()
    assert [t["name"] for t in tools] == ["shop.search"]


async def test_detect_malformed_returns_empty():
    runtime = _runtime_with({"unexpected": "shape"})
    assert await runtime.detect_model_context() == []


def test_format_context_mentions_tools():
    from src.agent.planner import format_webmcp_context

    tools = [
        {"name": "shop.search", "description": "Search the catalog"},
        {"name": "shop.buy", "description": ""},
    ]
    ctx = format_webmcp_context(tools)
    assert "shop.search" in ctx
    assert "shop.buy" in ctx
    assert "Search the catalog" in ctx
    assert format_webmcp_context([]) == ""


class _FakeLLM:
    def __init__(self):
        self.messages = None

    async def complete(self, messages, role=None):
        self.messages = messages
        return "1. Open the page"


async def test_plan_includes_webmcp_context_when_given():
    llm = _FakeLLM()
    await planner.plan(
        "Document checkout",
        "<html></html>",
        "[]",
        llm,
        webmcp_context="WEBMCP TOOLS:\n- shop.search",
    )
    user_text = llm.messages[1]["content"]
    assert "WEBMCP TOOLS" in user_text
    assert "shop.search" in user_text


async def test_plan_omits_section_when_no_context():
    llm = _FakeLLM()
    await planner.plan("Document checkout", "<html></html>", "[]", llm)
    assert "WEBMCP" not in llm.messages[1]["content"]


def test_flag_defaults_off(monkeypatch):
    import main as main_mod

    monkeypatch.delenv("READY_AI_WEBMCP_INGEST", raising=False)
    args = main_mod.resolve_run_args(
        main_mod.parse_args(["run", "--goal", "g", "--url", "https://x.test"])
    )
    assert args.webmcp_ingest is False


def test_flag_on_via_cli():
    import main as main_mod

    args = main_mod.resolve_run_args(
        main_mod.parse_args(
            ["run", "--goal", "g", "--url", "https://x.test", "--webmcp-ingest"]
        )
    )
    assert args.webmcp_ingest is True


def test_flag_on_via_env(monkeypatch):
    import main as main_mod

    monkeypatch.setenv("READY_AI_WEBMCP_INGEST", "1")
    args = main_mod.resolve_run_args(
        main_mod.parse_args(["run", "--goal", "g", "--url", "https://x.test"])
    )
    assert args.webmcp_ingest is True


async def test_loop_skips_detection_when_flag_off():
    from src.agent.loop import AgenticLoop

    loop = AgenticLoop(
        goal="g", url="https://x.test", output_dir="/tmp", run_id="t",
        webmcp_ingest=False,
    )
    runtime = _runtime_with({"present": True, "tools": [{"name": "a"}]})
    assert await loop._webmcp_context(runtime) == ""
    runtime.evaluate.assert_not_called()


async def test_loop_detects_when_flag_on():
    from src.agent.loop import AgenticLoop

    loop = AgenticLoop(
        goal="g", url="https://x.test", output_dir="/tmp", run_id="t",
        webmcp_ingest=True,
    )
    runtime = _runtime_with(
        {"present": True, "tools": [{"name": "shop.search"}]}
    )
    ctx = await loop._webmcp_context(runtime)
    assert "shop.search" in ctx
    runtime.evaluate.assert_called_once()
