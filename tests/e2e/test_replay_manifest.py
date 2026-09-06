"""Slice-3A real-browser E2E: compile a verified flow, replay it zero-LLM.

Runs a declarative flow against the local ``/spa`` fixture with real
Chrome (headless), compiles the passed result into a replay manifest,
then replays the manifest with every LLM construction rigged to explode.
Proves the replay path is deterministic and LLM-free end to end.
"""

from __future__ import annotations

import json
import socket

import pytest

from src.agent.loop import AgenticLoop
from src.agent.replay import (
    REPLAY_MANIFEST_KIND,
    REPLAY_MANIFEST_VERSION,
    compile_manifest,
    manifest_to_flow_spec,
    read_manifest,
    write_manifest,
)
from src.api.models import FlowAction, FlowAssertion, FlowSpec, FlowStepSpec

pytestmark = pytest.mark.e2e


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _flow(base_url: str) -> FlowSpec:
    return FlowSpec(
        name="spa-replay-source",
        url=f"{base_url}/spa",
        steps=[
            FlowStepSpec(
                name="Go to products",
                actions=[FlowAction(action="click", selector="#nav-products")],
                asserts=[
                    FlowAssertion(type="url_contains", expected="/spa/products"),
                    FlowAssertion(
                        type="text_contains",
                        expected="Products",
                        selector="#spa-status",
                    ),
                ],
            ),
        ],
    )


@pytest.mark.asyncio
async def test_compile_and_replay_zero_llm(e2e_server, tmp_path, cdp_port):
    flow = _flow(e2e_server)
    author = AgenticLoop(
        goal="e2e-replay-author",
        url=flow.url,
        output_dir=str(tmp_path),
        run_id="e2e-replay-author",
        headless=True,
        port=cdp_port,
    )
    authored = await author.run_flow(flow)
    assert authored["status"] == "passed", authored
    assert authored["steps"][0].get("fingerprint_pre"), authored

    manifest = compile_manifest(flow, authored)
    assert manifest["version"] == REPLAY_MANIFEST_VERSION
    assert manifest["kind"] == REPLAY_MANIFEST_KIND
    manifest_path = write_manifest(manifest, tmp_path, "e2e-replay-author")
    assert manifest_path.is_file()

    def _boom(*args, **kwargs):
        raise AssertionError("LLM must never be constructed during replay")

    import src.llm.client as llm_client_module

    _original = llm_client_module.LLMClient
    llm_client_module.LLMClient = _boom
    try:
        replayed_flow = manifest_to_flow_spec(read_manifest(manifest_path))
        player = AgenticLoop(
            goal="e2e-replay",
            url=replayed_flow.url,
            output_dir=str(tmp_path),
            run_id="e2e-replay",
            headless=True,
            port=_free_port(),
        )
        replayed = await player.run_flow(replayed_flow, allow_llm=False)
    finally:
        llm_client_module.LLMClient = _original
    assert replayed["status"] == "passed", replayed
    assert replayed["steps"][0]["status"] == "passed"

    # The manifest on disk is complete and self-describing.
    blob = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert blob["steps"][0]["fingerprint_pre"]
    assert blob["steps"][0]["idempotency_key"] == "e2e-replay-author:step-1"
