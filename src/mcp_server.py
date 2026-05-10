"""
MCP Server (Model Context Protocol) for ready-ai.

Exposes ready-ai capabilities as MCP tools so any MCP client
(Cursor, Claude Desktop, Windsurf, etc.) can generate and test
documentation.

Transport: stdio (default for MCP)
"""

import asyncio
import json
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

# ─── MCP Protocol Helpers ────────────────────────────────────────────────


def _send_message(msg: dict[str, Any]) -> None:
    """Send a JSON-RPC message over stdout."""
    payload = json.dumps(msg, separators=(",", ":"))
    sys.stdout.write(payload + "\n")
    sys.stdout.flush()


def _read_messages():
    """Yield JSON-RPC messages from stdin."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON: {line[:100]}")


# ─── Tool Definitions ────────────────────────────────────────────────────


TOOLS = [
    {
        "name": "ready_ai_generate_docs",
        "description": "Generate documentation for a web application by navigating and capturing screenshots.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "What to document (e.g., 'User login flow on SaaS dashboard')",
                },
                "url": {
                    "type": "string",
                    "description": "Base URL of the application to document",
                },
                "language": {
                    "type": "string",
                    "description": "Documentation language (pt, en, es, fr)",
                    "default": "en",
                },
                "headless": {
                    "type": "boolean",
                    "description": "Run browser headless",
                    "default": True,
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Maximum number of steps to document",
                    "default": 15,
                },
            },
            "required": ["goal", "url"],
        },
    },
    {
        "name": "ready_ai_test_docs",
        "description": "Test existing documentation against a live UI to detect drift or breakage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc_path": {
                    "type": "string",
                    "description": "Path to the docs.md file to test",
                },
                "url": {
                    "type": "string",
                    "description": "URL to test against",
                },
                "auto_heal": {
                    "type": "boolean",
                    "description": "Auto-update docs when drift is detected",
                    "default": False,
                },
                "threshold": {
                    "type": "number",
                    "description": "Visual similarity threshold (0.0-1.0)",
                    "default": 0.85,
                },
            },
            "required": ["doc_path", "url"],
        },
    },
    {
        "name": "ready_ai_check_health",
        "description": "Check if the ready-ai service is healthy and ready.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ─── Tool Handlers ───────────────────────────────────────────────────────


async def _handle_generate_docs(params: dict[str, Any]) -> dict[str, Any]:
    """Execute a documentation run and return the result."""
    from src.agent.loop import AgenticLoop

    goal = params["goal"]
    url = params["url"]
    language = params.get("language", "en")
    headless = params.get("headless", True)

    loop = AgenticLoop(
        goal=goal,
        url=url,
        model="gpt-4o-mini",
        headless=headless,
        language=language,
    )

    try:
        result_path = await loop.run()
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"✅ Documentation generated!\n\nSaved to: {result_path}\n\nGoal: {goal}\nURL: {url}",
                }
            ],
            "isError": False,
        }
    except Exception as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"❌ Documentation generation failed: {e}",
                }
            ],
            "isError": True,
        }


async def _handle_test_docs(params: dict[str, Any]) -> dict[str, Any]:
    """Test documentation and return the report."""
    from src.agent.test_runner import DocTestRunner

    doc_path = params["doc_path"]
    url = params["url"]
    auto_heal = params.get("auto_heal", False)
    threshold = params.get("threshold", 0.85)

    runner = DocTestRunner(
        doc_path=doc_path,
        url=url,
        auto_heal=auto_heal,
        threshold=threshold,
    )

    try:
        report = await runner.run()
        status_emoji = {
            "PASSED": "✅",
            "DRIFT_DETECTED": "⚠️",
            "BROKEN": "❌",
            "HEALED": "🩹",
        }.get(report.overall_status, "❓")

        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"{status_emoji} Test Result: {report.overall_status}\n\n"
                        f"Steps tested: {len(report.results)}\n"
                        f"Drift detected: {len(report.steps_outdated)}\n"
                        f"Broken: {len(report.steps_broken)}\n"
                        f"Healed: {getattr(report, 'steps_healed', 0)}\n"
                    ),
                }
            ],
            "isError": report.overall_status == "BROKEN",
        }
    except Exception as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"❌ Test failed: {e}",
                }
            ],
            "isError": True,
        }


async def _handle_check_health(_params: dict[str, Any]) -> dict[str, Any]:
    """Return service health status."""
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    "✅ ready-ai is healthy\n\n"
                    "Capabilities:\n"
                    "- generate_docs: Document any web UI\n"
                    "- test_docs: Detect UI drift automatically\n"
                    "- check_health: This check\n"
                ),
            }
        ],
        "isError": False,
    }


TOOL_HANDLERS = {
    "ready_ai_generate_docs": _handle_generate_docs,
    "ready_ai_test_docs": _handle_test_docs,
    "ready_ai_check_health": _handle_check_health,
}


# ─── MCP Server Loop ─────────────────────────────────────────────────────


async def run_mcp_server() -> None:
    """Run the MCP server over stdio."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler("mcp-server.log")],
    )

    logger.info("MCP Server starting...")

    for msg in _read_messages():
        msg_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params", {})

        if method == "initialize":
            _send_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "ready-ai-mcp",
                        "version": "0.1.0",
                    },
                },
            })

        elif method == "tools/list":
            _send_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS},
            })

        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_params = params.get("arguments", {})
            handler = TOOL_HANDLERS.get(tool_name)

            if handler is None:
                _send_message({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32601,
                        "message": f"Unknown tool: {tool_name}",
                    },
                })
                continue

            try:
                result = await handler(tool_params)
                _send_message({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": result,
                })
            except Exception as exc:
                logger.exception(f"Tool {tool_name} failed")
                _send_message({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Internal error: {exc}",
                            }
                        ],
                        "isError": True,
                    },
                })

        elif method == "ping":
            _send_message({"jsonrpc": "2.0", "id": msg_id, "result": {}})

        else:
            _send_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}",
                },
            })


if __name__ == "__main__":
    asyncio.run(run_mcp_server())
