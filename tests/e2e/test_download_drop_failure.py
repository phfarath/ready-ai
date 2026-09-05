"""Slice-3 real-browser E2E: download, CDP drop, truthful failure.

Runs against the local fixture servers with real Chrome (headless).
No LLM, no creds, no screenshots.

- download: session-level drive — allow download into tmp_path via
  ``Browser.setDownloadBehavior``, click the link, assert the
  ``Page.downloadWillBegin`` event plus the file on disk. Productizing the
  behavior config (allowlist, filename/size/MIME checks) is T-7 work; the
  engine event path is proven here.
- CDP drop: kill the Chrome process mid-flow (during a long `wait`) and
  assert the run reports a terminal disconnect: step in flight fails with
  the disconnect reason, later steps report ``skipped`` — never a hang,
  never a silent green.
- truthful failure: a missing selector fails fast with the selector in the
  reason, and a typed password never leaks into the result (masked at the
  report boundary).

Skipped automatically when no Chrome binary is present (see conftest).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.agent.loop import AgenticLoop
from src.api.models import FlowAction, FlowAssertion, FlowSpec, FlowStepSpec

pytestmark = pytest.mark.e2e


def _loop(url: str, *, tmp_path, cdp_port: int, run_id: str) -> AgenticLoop:
    return AgenticLoop(
        goal="e2e-slice3",
        url=url,
        output_dir=str(tmp_path),
        run_id=run_id,
        headless=True,
        port=cdp_port,
    )


@pytest.mark.asyncio
async def test_download_completes_to_configured_dir(e2e_server, tmp_path, cdp_port):
    loop = _loop(f"{e2e_server}/downloads", tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-download")
    await loop._session.setup()
    try:
        page = loop._session.page
        await page.enable()
        await page.navigate(f"{e2e_server}/downloads")
        await loop._session.conn.send(
            "Browser.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(tmp_path)},
        )
        waiter = asyncio.create_task(
            page.wait_for_download(filename="report.csv", timeout=20.0)
        )
        await asyncio.sleep(0.2)
        assert await loop._session.input_domain.click("#dl-link") is True
        evidence = await waiter
        assert evidence is not None, "Page.downloadWillBegin not observed"
        assert evidence.details["filename"] == "report.csv"
        target = tmp_path / "report.csv"
        for _ in range(40):
            if target.exists():
                break
            await asyncio.sleep(0.25)
        assert target.exists(), "downloaded file missing on disk"
        assert "alice" in target.read_text(encoding="utf-8")
    finally:
        await loop._session.teardown()


@pytest.mark.asyncio
async def test_chrome_kill_reports_terminal_disconnect(e2e_server, tmp_path, cdp_port):
    flow = FlowSpec(
        name="kill-midflow",
        url=f"{e2e_server}/spa",
        steps=[
            FlowStepSpec(
                name="Quick click",
                actions=[FlowAction(action="click", selector="#nav-products")],
                asserts=[
                    FlowAssertion(type="text_contains", expected="Products", selector="#spa-status"),
                ],
            ),
            FlowStepSpec(
                name="Long wait (killed mid-flight)",
                actions=[FlowAction(action="wait", selector="#never-appears", retries=0)],
            ),
            FlowStepSpec(
                name="Never runs",
                actions=[FlowAction(action="navigate", url=f"{e2e_server}/shadow")],
            ),
        ],
    )
    loop = _loop(flow.url, tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-kill")
    task = asyncio.create_task(loop.run_flow(flow))
    # Wait for Chrome to be up, then give step 1 time to pass so the kill
    # lands mid-flight in step 2 (never in setup or step 1).
    for _ in range(40):
        if loop._session._chrome_proc is not None:
            break
        await asyncio.sleep(0.25)
    await asyncio.sleep(3.0)
    loop._session._chrome_proc.kill()
    result = await asyncio.wait_for(task, timeout=90.0)
    assert result["status"] == "failed", result
    assert result["steps"][0]["status"] == "passed"
    assert "CDP connection lost" in result["steps"][1]["failure_reason"]
    assert result["steps"][2]["status"] == "skipped"
    assert "CDP connection lost" in result["failure_reason"]


@pytest.mark.asyncio
async def test_failed_run_stays_truthful_and_sanitized(e2e_server, tmp_path, cdp_port):
    secret = "s3cret-e2e-pw"
    flow = FlowSpec(
        name="bad-selector",
        url=f"{e2e_server}/login",
        steps=[
            FlowStepSpec(
                name="Type credentials",
                actions=[
                    FlowAction(action="type", selector="#login-email", text="user@example.com"),
                    FlowAction(action="type", selector="#login-pass", text=secret),
                ],
            ),
            FlowStepSpec(
                name="Click missing element",
                actions=[FlowAction(action="click", selector="#missing", retries=0)],
            ),
        ],
    )
    loop = _loop(flow.url, tmp_path=tmp_path, cdp_port=cdp_port, run_id="e2e-failure")
    result = await loop.run_flow(flow)
    assert result["status"] == "failed", result
    assert result["steps"][0]["status"] == "passed"
    assert "#missing" in result["steps"][1]["failure_reason"]
    dumped = json.dumps(result)
    assert secret not in dumped
    assert "***" in dumped
