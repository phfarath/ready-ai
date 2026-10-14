"""READY-AI-T-9: sanitized flight recorder for terminal automation failures.

DoD coverage:
- Any terminal failure produces a bundle correlated to run + step.
- The bundle holds only allowed fields within configurable limits.
- Console/exception and network events are tied to the right target/session.
- A test proves secret redaction and the absence of response bodies.
- A fixture failure is diagnosable without re-running.
"""

from __future__ import annotations

import base64
import json
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.loop import AgenticLoop
from src.api.models import FlowAction, FlowAssertion, FlowSpec, FlowStepSpec
from src.cdp.connection import CDPEventContext, _history_safe_event
from src.cdp.page import PassiveEvidence
from src.observability import (
    FlightRecorderConfig,
    capture_failure_bundle,
)


class _FakeConn:
    """Minimal stand-in for CDPConnection used by the recorder."""

    def __init__(self, history=(), state=None):
        self._event_history = deque(history, maxlen=512)
        self.state = state
        self._consecutive_failures = 0


def _make_loop(tmp_path, run_id="fr-test"):
    """AgenticLoop with a fully mocked BrowserSession (mirrors test_agent_flow)."""
    loop = AgenticLoop(
        goal="run-flow",
        url="https://app.example.com/start?sid=sekret",
        output_dir=str(tmp_path),
        run_id=run_id,
        headless=True,
    )
    session = loop._session
    session.setup = AsyncMock(return_value=None)
    session.teardown = AsyncMock(return_value=None)
    session.inject_cookies = AsyncMock(return_value=None)
    session.handle_login = AsyncMock(return_value=None)
    session.cookies_file = None
    session.username = None
    session.password = None

    page = MagicMock()
    page.enable = AsyncMock(return_value=None)
    page.navigate = AsyncMock(return_value=None)
    page.wait_for_network_idle = AsyncMock(return_value=None)
    page.get_dom_html = AsyncMock(
        return_value='<html><body><input type="password" value="***"></body></html>'
    )
    page.screenshot = AsyncMock(
        return_value=base64.b64encode(b"png-bytes").decode()
    )
    page.http_failures_since = MagicMock(
        return_value=[
            PassiveEvidence(
                kind="network_http",
                passed=False,
                observed="HTTP 500",
                details={"status": 500, "url": "https://app.example.com/api"},
            )
        ]
    )
    page.context = None

    runtime = MagicMock()
    runtime.evaluate = AsyncMock(return_value=None)
    runtime.get_state_fingerprint = AsyncMock(return_value="fingerprint-abc")

    session._page = page
    session._runtime = runtime
    session._input = MagicMock()

    loop._save_checkpoint = MagicMock(return_value=None)
    return loop, page, runtime, session._input


def _failing_flow() -> FlowSpec:
    return FlowSpec(
        name="checkout",
        url="https://app.example.com/start",
        steps=[
            FlowStepSpec(
                name="Confirm order",
                actions=[
                    FlowAction(
                        action="navigate", url="https://app.example.com/confirm"
                    )
                ],
                asserts=[
                    FlowAssertion(
                        type="url_contains",
                        expected="/done",
                        message="Order page was not reached",
                    )
                ],
            )
        ],
    )


def _read_bundle(tmp_path, run_id="fr-test"):
    path = tmp_path / "flight_recorder" / f"{run_id}_failure.json"
    assert path.exists(), f"bundle missing at {path}"
    return path, json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_flow_failure_writes_bundle_correlated_to_run_and_step(
    tmp_path, monkeypatch
):
    """DoD1 — terminal step failure produces a bundle keyed by run+step."""
    loop, page, runtime, _ = _make_loop(tmp_path)
    monkeypatch.setattr(
        "src.agent.loop.executor._dispatch_action",
        AsyncMock(return_value="Navigated to: https://app.example.com/confirm"),
    )
    runtime.evaluate.side_effect = lambda expression, **_: (
        "https://app.example.com/cancelled?x=1"
        if "window.location.href" in expression
        else None
    )

    result = await loop.run_flow(_failing_flow())

    assert result["status"] == "failed"
    path, bundle = _read_bundle(tmp_path)
    assert bundle["schema"] == "ready-ai.flight-recorder/v1"
    assert bundle["run_id"] == "fr-test"
    assert bundle["kind"] == "flow"
    assert bundle["flow"] == "checkout"
    assert bundle["status"] == "FAILED"
    assert bundle["failure_reason"] == "Order page was not reached"
    assert bundle["step"]["index"] == 1
    assert bundle["step"]["name"] == "Confirm order"
    # URLs carry no query strings or userinfo.
    assert bundle["url"]["initial"] == "https://app.example.com/start"
    assert bundle["url"]["current"] == "https://app.example.com/cancelled"
    assert "sekret" not in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_bundle_is_diagnosable_without_rerun(tmp_path, monkeypatch):
    """DoD5 — the bundle alone explains the failure (no re-execution)."""
    loop, page, runtime, _ = _make_loop(tmp_path)
    monkeypatch.setattr(
        "src.agent.loop.executor._dispatch_action",
        AsyncMock(return_value="Navigated to: https://app.example.com/confirm"),
    )
    runtime.evaluate.side_effect = lambda expression, **_: (
        "https://app.example.com/cancelled"
        if "window.location.href" in expression
        else None
    )
    loop._session._conn = _FakeConn(
        history=[
            (
                7,
                _history_safe_event(
                    {
                        "method": "Runtime.exceptionThrown",
                        "sessionId": "s-1",
                        "params": {
                            "exceptionDetails": {
                                "text": "Uncaught TypeError",
                                "url": "https://app.example.com/app.js?token=abc",
                                "lineNumber": 42,
                                "exception": {
                                    "className": "TypeError",
                                    "description": "x is not a function",
                                },
                            }
                        },
                    }
                ),
            )
        ],
        state=MagicMock(value="healthy"),
    )
    page.context = CDPEventContext(session_id="s-1")

    await loop.run_flow(_failing_flow())

    _, bundle = _read_bundle(tmp_path)
    # Enough context to diagnose: what step, why, where the page was,
    # what the DOM looked like, what the page threw.
    assert bundle["dom_excerpt"].startswith("<html>")
    assert bundle["state_fingerprint"] == "fingerprint-abc"
    assert bundle["screenshot_b64"]
    assert bundle["console"][0]["method"] == "Runtime.exceptionThrown"
    assert bundle["console"][0]["text"] == "x is not a function"
    assert bundle["console"][0]["session_id"] == "s-1"
    # Exception URL is query-stripped.
    assert bundle["console"][0]["url"] == "https://app.example.com/app.js"
    assert bundle["network_failures"][0]["details"]["status"] == 500
    assert bundle["connection"]["state"] == "healthy"
    assert bundle["timings"]["run_id"] == "fr-test"


@pytest.mark.asyncio
async def test_no_bundle_on_success(tmp_path, monkeypatch):
    """A passing run must not produce a failure bundle."""
    loop, _, runtime, _ = _make_loop(tmp_path)
    monkeypatch.setattr(
        "src.agent.loop.executor._dispatch_action",
        AsyncMock(return_value="Navigated to: https://app.example.com/done"),
    )
    runtime.evaluate.side_effect = lambda expression, **_: (
        "https://app.example.com/done"
        if "window.location.href" in expression
        else None
    )
    flow = FlowSpec(
        name="ok",
        url="https://app.example.com/start",
        steps=[
            FlowStepSpec(
                name="s1",
                actions=[FlowAction(action="navigate", url="https://x.example.com")],
                asserts=[FlowAssertion(type="url_contains", expected="/done")],
            )
        ],
    )
    result = await loop.run_flow(flow)
    assert result["status"] == "passed"
    assert not (tmp_path / "flight_recorder" / "fr-test_failure.json").exists()


@pytest.mark.asyncio
async def test_run_flow_exception_path_still_writes_bundle(tmp_path, monkeypatch):
    """DoD1 — an unhandled run-level exception also produces a bundle."""
    loop, page, runtime, _ = _make_loop(tmp_path)
    loop._session.setup = AsyncMock(
        side_effect=RuntimeError("Chrome refused to start")
    )

    with pytest.raises(RuntimeError, match="refused to start"):
        await loop.run_flow(_failing_flow())

    _, bundle = _read_bundle(tmp_path)
    assert bundle["kind"] == "flow"
    assert "refused to start" in bundle["failure_reason"]
    assert bundle["url"]["initial"] == "https://app.example.com/start"


@pytest.mark.asyncio
async def test_secret_redaction_and_no_response_bodies(tmp_path, monkeypatch):
    """DoD4 — secrets are redacted; response bodies never enter the bundle."""
    # Deliberately fake low-entropy marker — real redaction only needs a
    # recognizable string. High-entropy lookalikes trip secret scanners.
    secret = "TESTONLY-NOT-A-REAL-SECRET-redact-me"
    conn = _FakeConn()
    page = MagicMock()
    page.get_dom_html = AsyncMock(return_value="<html/>")
    page.screenshot = AsyncMock(return_value="")
    page.http_failures_since = MagicMock(return_value=[])
    page.context = None
    runtime = MagicMock()
    runtime.evaluate = AsyncMock(return_value="https://app.example.com/x")
    runtime.get_state_fingerprint = AsyncMock(return_value="fp")

    path = await capture_failure_bundle(
        output_dir=str(tmp_path),
        run_id="redact-test",
        failure_reason="boom",
        step={"index": 1, "name": "login", "password": secret},
        initial_url="https://app.example.com/x?token=" + secret,
        page=page,
        runtime=runtime,
        connection=conn,
    )

    assert path is not None
    raw = path.read_text(encoding="utf-8")
    assert secret not in raw
    assert "***REDACTED***" in raw

    # Response bodies/postData are stripped at ingestion by
    # _history_safe_event — the bundle can only ever carry metadata.
    event = _history_safe_event(
        {
            "method": "Network.responseReceived",
            "sessionId": "s-1",
            "params": {
                "response": {
                    "status": 200,
                    "url": "https://api.example.com/data?key=" + secret,
                    "headers": {"authorization": "Bearer " + secret},
                    "body": secret,
                }
            },
        }
    )
    assert event["params"]["response"] == {
        "status": 200,
        "url": "https://api.example.com/data",
    }

    request_event = _history_safe_event(
        {
            "method": "Network.requestWillBeSent",
            "sessionId": "s-1",
            "params": {
                "request": {
                    "method": "POST",
                    "url": "https://api.example.com/login?p=" + secret,
                    "headers": {"cookie": secret},
                    "postData": secret,
                }
            },
        }
    )
    assert request_event["params"]["request"] == {
        "method": "POST",
        "url": "https://api.example.com/login",
    }


def test_history_retains_console_and_exception_events_sanitized():
    """DoD3 — console/exception rows survive the safe-history whitelist."""
    console = _history_safe_event(
        {
            "method": "Runtime.consoleAPICalled",
            "sessionId": "s-1",
            "params": {
                "type": "error",
                "args": [
                    {"type": "string", "value": "boom happened"},
                    {
                        "type": "object",
                        "preview": {"huge": "payload" * 100},
                    },
                ],
            },
        }
    )
    assert console["params"]["type"] == "error"
    assert console["params"]["args"][0]["value"] == "boom happened"
    # Remote objects keep only their type tag — never the payload.
    assert console["params"]["args"][1] == {"type": "object"}

    thrown = _history_safe_event(
        {
            "method": "Runtime.exceptionThrown",
            "sessionId": "s-1",
            "params": {
                "exceptionDetails": {
                    "text": "Uncaught",
                    "url": "https://a.example.com/x.js?cb=1",
                    "lineNumber": 7,
                    "exception": {
                        "className": "ReferenceError",
                        "description": "y is not defined",
                        "object": {"deep": "payload"},
                    },
                }
            },
        }
    )
    details = thrown["params"]["exceptionDetails"]
    assert details["text"] == "Uncaught"
    assert details["url"] == "https://a.example.com/x.js"
    assert details["exception"] == {
        "className": "ReferenceError",
        "description": "y is not defined",
    }


@pytest.mark.asyncio
async def test_console_events_stay_scoped_to_the_page_session(tmp_path):
    """DoD3 — a scoped page only records its own session's console rows."""
    events = [
        (
            1,
            _history_safe_event(
                {
                    "method": "Runtime.consoleAPICalled",
                    "sessionId": "s-1",
                    "params": {
                        "type": "log",
                        "args": [{"type": "string", "value": "from-main"}],
                    },
                }
            ),
        ),
        (
            2,
            _history_safe_event(
                {
                    "method": "Runtime.consoleAPICalled",
                    "sessionId": "s-2",
                    "params": {
                        "type": "log",
                        "args": [{"type": "string", "value": "from-popup"}],
                    },
                }
            ),
        ),
    ]
    conn = _FakeConn(history=events)
    page = MagicMock()
    page.get_dom_html = AsyncMock(return_value="<html/>")
    page.screenshot = AsyncMock(return_value="")
    page.http_failures_since = MagicMock(return_value=[])
    page.context = CDPEventContext(session_id="s-1")

    path = await capture_failure_bundle(
        output_dir=str(tmp_path),
        run_id="scope-test",
        page=page,
        connection=conn,
    )
    bundle = json.loads(path.read_text(encoding="utf-8"))
    assert [row["session_id"] for row in bundle["console"]] == ["s-1"]
    assert bundle["console"][0]["text"] == "from-main"


@pytest.mark.asyncio
async def test_dead_browser_still_produces_bundle_with_capture_errors(
    tmp_path,
):
    """A crashed connection must not prevent the bundle from landing."""
    conn = _FakeConn(state=MagicMock(value="down"))
    page = MagicMock()
    page.get_dom_html = AsyncMock(side_effect=RuntimeError("target closed"))
    page.screenshot = AsyncMock(side_effect=RuntimeError("target closed"))
    page.http_failures_since = MagicMock(side_effect=RuntimeError("gone"))
    page.context = None
    runtime = MagicMock()
    runtime.evaluate = AsyncMock(side_effect=RuntimeError("target closed"))
    runtime.get_state_fingerprint = AsyncMock(
        side_effect=RuntimeError("target closed")
    )

    path = await capture_failure_bundle(
        output_dir=str(tmp_path),
        run_id="dead-browser",
        failure_reason="aborted: CDP connection lost",
        page=page,
        runtime=runtime,
        connection=conn,
    )

    assert path is not None
    bundle = json.loads(path.read_text(encoding="utf-8"))
    assert bundle["failure_reason"] == "aborted: CDP connection lost"
    assert bundle["connection"]["state"] == "down"
    assert len(bundle["capture_errors"]) >= 3


@pytest.mark.asyncio
async def test_retention_prunes_oldest_bundles(tmp_path, monkeypatch):
    """DoD2 — retention bound: only max_bundles files are kept."""
    import os

    monkeypatch.setenv("READY_AI_FR_MAX_BUNDLES", "2")
    cfg = FlightRecorderConfig.from_env()
    for i in range(3):
        path = await capture_failure_bundle(
            output_dir=str(tmp_path), run_id=f"run-{i}", config=cfg
        )
        assert path is not None
        # Force distinct, increasing mtimes so ordering is deterministic.
        os.utime(path, (1_000_000 + i, 1_000_000 + i))

    remaining = sorted(
        (tmp_path / "flight_recorder").glob("*_failure.json")
    )
    assert [p.name for p in remaining] == [
        "run-1_failure.json",
        "run-2_failure.json",
    ]


@pytest.mark.asyncio
async def test_disabled_env_skips_capture(tmp_path, monkeypatch):
    monkeypatch.setenv("READY_AI_FR_DISABLED", "1")
    path = await capture_failure_bundle(
        output_dir=str(tmp_path), run_id="off", config=None
    )
    assert path is None
    assert not (tmp_path / "flight_recorder").exists()


@pytest.mark.asyncio
async def test_screenshot_cap_omits_oversized_capture(tmp_path):
    page = MagicMock()
    page.get_dom_html = AsyncMock(return_value="<html/>")
    page.screenshot = AsyncMock(return_value="A" * 4_000_000)  # ~3MB decoded
    page.http_failures_since = MagicMock(return_value=[])
    page.context = None

    path = await capture_failure_bundle(
        output_dir=str(tmp_path),
        run_id="big-shot",
        page=page,
        config=FlightRecorderConfig(max_screenshot_bytes=1_000_000),
    )
    bundle = json.loads(path.read_text(encoding="utf-8"))
    assert bundle["screenshot_b64"].startswith("omitted:")
