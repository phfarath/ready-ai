"""Slice-2D real-browser E2E: SSO human checkpoint pause/resume.

Runs a declarative flow against the ``/sso`` fixture with real Chrome
(headless). The engine must NOT automate the challenge: it pauses at
``await_human`` with an observable resume condition, persists a PAUSED
checkpoint, and only continues after the human (played here by arming the
fixture IdP state) acts — resumed by run_id.

Also proves no secret material reaches the checkpoint or result files.
"""

from __future__ import annotations

import json
import socket
import urllib.request

import pytest

from src.agent.loop import AgenticLoop
from src.api.models import FlowAction, FlowAssertion, FlowSpec, FlowStepSpec

pytestmark = pytest.mark.e2e


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _post(base_url: str, path: str) -> None:
    req = urllib.request.Request(base_url + path, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200


def _flow(base_url: str) -> FlowSpec:
    return FlowSpec(
        name="sso-checkpoint",
        url=f"{base_url}/sso",
        steps=[
            FlowStepSpec(
                name="Hit the SSO challenge",
                actions=[FlowAction(action="observe")],
                asserts=[
                    FlowAssertion(type="element_present", selector="#sso-challenge"),
                ],
            ),
            FlowStepSpec(
                name="Human completes SSO",
                actions=[
                    FlowAction(
                        action="await_human",
                        reason="Complete the SSO login in your own browser",
                        resume_when={"selector_visible": "#authed"},
                    )
                ],
            ),
            FlowStepSpec(
                name="Verify authenticated landing",
                actions=[FlowAction(action="observe")],
                asserts=[
                    FlowAssertion(type="element_present", selector="#authed"),
                ],
            ),
        ],
    )


@pytest.mark.asyncio
async def test_sso_pause_resume_and_no_secret_leak(e2e_server, tmp_path, cdp_port):
    _post(e2e_server, "/sso/reset")
    run_id = "e2e-sso"
    flow = _flow(e2e_server)

    paused_loop = AgenticLoop(
        goal="e2e-sso",
        url=flow.url,
        output_dir=str(tmp_path),
        run_id=run_id,
        headless=True,
        port=cdp_port,
    )
    paused_result = await paused_loop.run_flow(flow)
    assert paused_result["status"] == "paused", paused_result
    assert paused_result["steps"][0]["status"] == "passed"
    assert paused_result["steps"][1]["status"] == "paused"
    pause = paused_result["pause"]
    assert pause["reason"] == "Complete the SSO login in your own browser"
    assert pause["resume_when"] == {"selector_visible": "#authed"}
    assert pause["step_index"] == 2
    checkpoint = tmp_path / f"{run_id}_state.json"
    assert pause["checkpoint"] == str(checkpoint)
    assert checkpoint.is_file()
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert saved["status"] == "PAUSED"
    assert saved["current_step_index"] == 2
    assert saved["resume_when"] == {"selector_visible": "#authed"}
    assert paused_result["steps"][2]["status"] == "skipped"

    # The human acts (outside the engine): the IdP now reports authed.
    _post(e2e_server, "/sso/arm")

    resumed_loop = AgenticLoop(
        goal="e2e-sso",
        url=flow.url,
        output_dir=str(tmp_path),
        run_id=run_id,
        headless=True,
        port=_free_port(),
        resume_from=str(checkpoint),
    )
    resumed_result = await resumed_loop.run_flow(flow)
    assert resumed_result["status"] == "passed", resumed_result
    assert resumed_result["summary"]["resumed_from"] == str(checkpoint)
    assert resumed_result["steps"][0]["status"] == "skipped"
    assert (
        resumed_result["steps"][1].get("confirmation")
        == "human-checkpoint-resumed"
    )
    assert resumed_result["steps"][2]["status"] == "passed"

    # No secret material anywhere on disk (the challenge is unauthenticated
    # here, but the invariant must hold for credentialed runs too).
    blob = (tmp_path / f"{run_id}_flow_result.json").read_text(encoding="utf-8")
    assert "password" not in blob.lower()
    state_blob = checkpoint.read_text(encoding="utf-8")
    assert "password" not in state_blob.lower()
