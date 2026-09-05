"""Slice-2C real-browser E2E: upload, verified download, native dialogs.

Runs declarative flows through ``AgenticLoop.run_flow`` against the local
fixture servers with real Chrome (headless). No LLM, no creds, no screenshots.

- upload: a real file from tmp_path under an explicit roots allowlist is
  attached to a file input (change event proves it); a path outside the
  roots fails closed without touching the page.
- download: the productized `download` action clicks the link, observes
  the CDP event and verifies the file on disk (name + non-empty).
- dialogs: native alert/confirm/prompt handled with explicit accept/dismiss
  decisions through the nested `then` trigger.

Skipped automatically when no Chrome binary is present (see conftest).
"""

from __future__ import annotations

import pytest

from src.agent.loop import AgenticLoop
from src.api.models import FlowAction, FlowAssertion, FlowSpec, FlowStepSpec

pytestmark = pytest.mark.e2e


def _loop(url: str, *, tmp_path, cdp_port: int, run_id: str) -> AgenticLoop:
    return AgenticLoop(
        goal="e2e-files",
        url=url,
        output_dir=str(tmp_path),
        run_id=run_id,
        headless=True,
        port=cdp_port,
    )


@pytest.mark.asyncio
async def test_upload_inside_allowlist(e2e_server, tmp_path, cdp_port):
    payload = tmp_path / "upload-me.txt"
    payload.write_text("ready-ai e2e upload\n")
    flow = FlowSpec(
        name="upload-ok",
        url=f"{e2e_server}/upload",
        steps=[
            FlowStepSpec(
                name="Attach file",
                actions=[
                    FlowAction(
                        action="upload",
                        selector="#file-input",
                        paths=[str(payload)],
                        roots=[str(tmp_path)],
                    )
                ],
                asserts=[
                    FlowAssertion(
                        type="text_contains",
                        expected="upload-me.txt",
                        selector="#upload-status",
                    ),
                ],
            )
        ],
    )
    loop = _loop(flow.url, tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-upload")
    result = await loop.run_flow(flow)
    assert result["status"] == "passed", result


@pytest.mark.asyncio
async def test_upload_outside_allowlist_fails_closed(e2e_server, tmp_path, cdp_port):
    payload = tmp_path / "upload-me.txt"
    payload.write_text("ready-ai e2e upload\n")
    denied = tmp_path / "denied"
    denied.mkdir()
    flow = FlowSpec(
        name="upload-deny",
        url=f"{e2e_server}/upload",
        steps=[
            FlowStepSpec(
                name="Attach outside roots",
                actions=[
                    FlowAction(
                        action="upload",
                        selector="#file-input",
                        paths=[str(payload)],
                        roots=[str(denied)],
                    )
                ],
            ),
            FlowStepSpec(
                name="Page untouched",
                actions=[FlowAction(action="observe")],
                asserts=[
                    FlowAssertion(
                        type="text_contains", expected="none", selector="#upload-status"
                    ),
                ],
            ),
        ],
    )
    loop = _loop(flow.url, tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-upload-deny")
    result = await loop.run_flow(flow)
    assert result["status"] == "failed", result
    assert "outside allowlist" in result["steps"][0]["failure_reason"]
    assert result["steps"][1]["status"] == "passed"


@pytest.mark.asyncio
async def test_download_action_verifies_file(e2e_server, tmp_path, cdp_port):
    flow = FlowSpec(
        name="download-verify",
        url=f"{e2e_server}/downloads",
        steps=[
            FlowStepSpec(
                name="Download report",
                actions=[
                    FlowAction(
                        action="download",
                        selector="#dl-link",
                        filename="report.csv",
                        mime="text/csv",
                    )
                ],
                asserts=[
                    FlowAssertion(type="element_present", selector="#dl-link"),
                ],
            )
        ],
    )
    loop = _loop(flow.url, tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-dl-action")
    result = await loop.run_flow(flow)
    assert result["status"] == "passed", result
    assert (tmp_path / "report.csv").exists()


@pytest.mark.asyncio
async def test_dialog_accept_confirm(e2e_server, tmp_path, cdp_port):
    flow = FlowSpec(
        name="dialog-accept",
        url=f"{e2e_server}/dialog-native",
        steps=[
            FlowStepSpec(
                name="Accept confirm",
                actions=[
                    FlowAction(
                        action="dialog",
                        decision="accept",
                        then={"action": "click", "selector": "#confirm-btn"},
                    )
                ],
                asserts=[
                    FlowAssertion(
                        type="text_contains", expected="true", selector="#dlg-result"
                    ),
                ],
            )
        ],
    )
    loop = _loop(flow.url, tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-dlg-ok")
    result = await loop.run_flow(flow)
    assert result["status"] == "passed", result


@pytest.mark.asyncio
async def test_dialog_dismiss_confirm(e2e_server, tmp_path, cdp_port):
    flow = FlowSpec(
        name="dialog-dismiss",
        url=f"{e2e_server}/dialog-native",
        steps=[
            FlowStepSpec(
                name="Dismiss confirm",
                actions=[
                    FlowAction(
                        action="dialog",
                        decision="dismiss",
                        then={"action": "click", "selector": "#confirm-btn"},
                    )
                ],
                asserts=[
                    FlowAssertion(
                        type="text_contains", expected="false", selector="#dlg-result"
                    ),
                ],
            )
        ],
    )
    loop = _loop(flow.url, tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-dlg-no")
    result = await loop.run_flow(flow)
    assert result["status"] == "passed", result


@pytest.mark.asyncio
async def test_dialog_prompt_with_text(e2e_server, tmp_path, cdp_port):
    flow = FlowSpec(
        name="dialog-prompt",
        url=f"{e2e_server}/dialog-native",
        steps=[
            FlowStepSpec(
                name="Answer prompt",
                actions=[
                    FlowAction(
                        action="dialog",
                        decision="accept",
                        text="hello",
                        then={"action": "click", "selector": "#prompt-btn"},
                    )
                ],
                asserts=[
                    FlowAssertion(
                        type="text_contains", expected="hello", selector="#dlg-result"
                    ),
                ],
            )
        ],
    )
    loop = _loop(flow.url, tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-dlg-text")
    result = await loop.run_flow(flow)
    assert result["status"] == "passed", result
