"""Upload allowlist, download progress, and JS dialog support (READY-AI-T-7).

All fixtures are Chrome-free: conn.send is routed by method name via
AsyncMock, and CDP events are injected with _publish_event.
"""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.cdp.connection import CDPConnection
from src.cdp.input import ENV_UPLOAD_ALLOWLIST, InputDomain
from src.cdp.page import PageDomain

ALLOWLIST_ENV = ENV_UPLOAD_ALLOWLIST


def _route_conn(routes: dict) -> tuple[CDPConnection, AsyncMock]:
    """CDPConnection double whose send() dispatches on the method name."""
    conn = CDPConnection()
    conn._ws = AsyncMock()
    conn.send = AsyncMock(side_effect=_make_route(routes))
    return conn, conn.send


def _make_route(routes):
    async def route(method, params=None, **kwargs):
        handler = routes.get(method)
        if handler is None:
            return {}
        if callable(handler):
            return handler(params or {})
        return handler

    return route


async def _publish_after_subscribed(coro_factory, conn, events):
    """Run a waiting coroutine and inject events once it is subscribed.

    Events must be published AFTER the waiter subscribes: live delivery keeps
    full payloads, while history replays are sanitized (guid/state/bytes are
    stripped by _history_safe_event).
    """
    task = asyncio.create_task(coro_factory())
    await asyncio.sleep(0)
    for event in events:
        await conn._publish_event(event)
    return await task


# ─── Upload: allowlist enforcement ───────────────────────────────────


@pytest.fixture
def upload_tree(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    inside_file = allowed / "doc.pdf"
    inside_file.write_text("pdf-bytes")
    rejected_file = outside / "secret.pdf"
    rejected_file.write_text("nope")
    return {"root": allowed, "outside": outside,
            "inside": inside_file, "rejected": rejected_file}


async def test_upload_inside_allowlist_sends_set_file_input_files(
    monkeypatch, upload_tree
):
    routes = {
        "DOM.getDocument": {"root": {"nodeId": 5}},
        "DOM.querySelector": {"nodeId": 77},
        "DOM.setFileInputFiles": {},
    }
    conn, send = _route_conn(routes)
    input_domain = InputDomain(conn)
    monkeypatch.setenv(ALLOWLIST_ENV, str(upload_tree["root"]))

    uploaded = await input_domain.set_file_input_files(
        "#avatar", [str(upload_tree["inside"])]
    )

    resolved = str(upload_tree["inside"].resolve())
    assert uploaded == [resolved]
    send.assert_any_call(
        "DOM.setFileInputFiles",
        {"files": [resolved], "nodeId": 77},
    )


async def test_upload_outside_allowlist_rejected_fail_closed_before_cdp(
    monkeypatch, upload_tree
):
    conn, send = _route_conn({})
    input_domain = InputDomain(conn)
    monkeypatch.setenv(ALLOWLIST_ENV, str(upload_tree["root"]))

    with pytest.raises(ValueError) as excinfo:
        await input_domain.set_file_input_files(
            "#avatar", [str(upload_tree["rejected"])]
        )

    message = str(excinfo.value)
    assert "outside the upload allowlist" in message
    assert "secret.pdf" in message
    # Allowed root is named, but the rejected file's own directory is not.
    assert str(upload_tree["outside"]) not in message
    send.assert_not_called()


async def test_upload_env_unset_rejects_everything(monkeypatch, upload_tree):
    monkeypatch.delenv(ALLOWLIST_ENV, raising=False)
    conn, send = _route_conn({})
    input_domain = InputDomain(conn)

    with pytest.raises(ValueError) as excinfo:
        await input_domain.set_file_input_files(
            "#avatar", [str(upload_tree["inside"])]
        )

    message = str(excinfo.value)
    assert ALLOWLIST_ENV in message
    assert "uploads are disabled" in message
    send.assert_not_called()


async def test_upload_empty_allowlist_rejects_everything(
    monkeypatch, upload_tree
):
    monkeypatch.setenv(ALLOWLIST_ENV, f"{os.pathsep}{os.pathsep} ")
    conn, send = _route_conn({})
    input_domain = InputDomain(conn)

    with pytest.raises(ValueError) as excinfo:
        await input_domain.set_file_input_files("#a", [str(upload_tree["inside"])])
    assert "unset or empty" in str(excinfo.value)
    send.assert_not_called()


async def test_upload_missing_file_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv(ALLOWLIST_ENV, str(tmp_path))
    conn, send = _route_conn({})
    input_domain = InputDomain(conn)

    ghost = tmp_path / "ghost.txt"
    with pytest.raises(ValueError) as excinfo:
        await input_domain.set_file_input_files("#f", [str(ghost)])
    assert "file not found" in str(excinfo.value)
    assert "ghost.txt" in str(excinfo.value)
    send.assert_not_called()


async def test_upload_directory_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv(ALLOWLIST_ENV, str(tmp_path))
    subdir = tmp_path / "folder"
    subdir.mkdir()
    conn, send = _route_conn({})
    input_domain = InputDomain(conn)

    with pytest.raises(ValueError) as excinfo:
        await input_domain.set_file_input_files("#f", [str(subdir)])
    assert "not a regular file" in str(excinfo.value)
    send.assert_not_called()


async def test_upload_selector_not_found_structured_error(
    monkeypatch, upload_tree
):
    routes = {
        "DOM.getDocument": {"root": {"nodeId": 5}},
        "DOM.querySelector": {"nodeId": 0},
    }
    conn, _ = _route_conn(routes)
    input_domain = InputDomain(conn)
    monkeypatch.setenv(ALLOWLIST_ENV, str(upload_tree["root"]))

    with pytest.raises(ValueError) as excinfo:
        await input_domain.set_file_input_files(
            "#missing-input", [str(upload_tree["inside"])]
        )
    assert "element not found" in str(excinfo.value)
    assert "#missing-input" in str(excinfo.value)


async def test_upload_accepts_multiple_roots_via_pathsep(
    monkeypatch, tmp_path
):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    file_b = root_b / "b.bin"
    file_b.write_text("x")
    monkeypatch.setenv(
        ALLOWLIST_ENV, os.pathsep.join([str(root_a), str(root_b)])
    )
    routes = {
        "DOM.getDocument": {"root": {"nodeId": 1}},
        "DOM.querySelector": {"nodeId": 9},
        "DOM.setFileInputFiles": {},
    }
    conn, send = _route_conn(routes)
    input_domain = InputDomain(conn)

    uploaded = await input_domain.set_file_input_files("input", [str(file_b)])
    assert uploaded == [str(file_b.resolve())]
    send.assert_any_call(
        "DOM.setFileInputFiles",
        {"files": [str(file_b.resolve())], "nodeId": 9},
    )


# ─── Dialogs ─────────────────────────────────────────────────────────


async def test_wait_for_dialog_evidences_type_and_message():
    conn = CDPConnection()
    conn._ws = AsyncMock()
    page = PageDomain(conn)

    evidence = await _publish_after_subscribed(
        lambda: page.wait_for_dialog(timeout=2.0),
        conn,
        [
            {
                "method": "Page.javascriptDialogOpening",
                "params": {"type": "confirm", "message": "Delete item?"},
            }
        ],
    )

    assert evidence is not None
    assert evidence.kind == "dialog"
    assert evidence.passed is True
    assert evidence.details["dialog_type"] == "confirm"
    assert evidence.details["message"] == "Delete item?"


async def test_wait_for_dialog_timeout_returns_none():
    conn = CDPConnection()
    conn._ws = AsyncMock()
    page = PageDomain(conn)

    evidence = await page.wait_for_dialog(timeout=0.05)
    assert evidence is None


async def test_handle_dialog_accept_minimal_params():
    conn, send = _route_conn({})
    page = PageDomain(conn)

    await page.handle_dialog(accept=True)

    send.assert_called_once_with("Page.handleJavaScriptDialog", {"accept": True})


async def test_handle_dialog_prompt_text_included_when_present():
    conn, send = _route_conn({})
    page = PageDomain(conn)

    await page.handle_dialog(accept=True, prompt_text="my answer")

    send.assert_called_once_with(
        "Page.handleJavaScriptDialog",
        {"accept": True, "promptText": "my answer"},
    )


async def test_handle_dialog_dismiss_without_prompt_text_has_no_leak():
    conn, send = _route_conn({})
    page = PageDomain(conn)

    await page.handle_dialog(accept=False)

    send.assert_called_once_with("Page.handleJavaScriptDialog", {"accept": False})


# ─── Download start → completion ─────────────────────────────────────


_BEGIN = {
    "method": "Page.downloadWillBegin",
    "params": {
        "guid": "guid-1",
        "suggestedFilename": "report.pdf",
        "url": "https://cdn.test/files/report.pdf?token=x",
    },
}


def _progress(state: str, **extra) -> dict:
    params = {"guid": "guid-1", "state": state}
    params.update(extra)
    return {"method": "Browser.downloadProgress", "params": params}


async def test_download_complete_success_with_matching_criteria():
    conn = CDPConnection()
    conn._ws = AsyncMock()
    page = PageDomain(conn)

    evidence = await _publish_after_subscribed(
        lambda: page.wait_for_download_complete(
            filename_contains="report",
            min_bytes=100,
            max_bytes=2048,
            timeout=5.0,
        ),
        conn,
        [_BEGIN, _progress("inProgress", receivedBytes=512), _progress(
            "completed", receivedBytes=1024, totalBytes=1024
        )],
    )

    assert evidence.passed is True
    assert evidence.observed == "download completed"
    assert evidence.details["filename"] == "report.pdf"
    assert evidence.details["state"] == "completed"
    assert evidence.details["receivedBytes"] == 1024
    assert evidence.details["totalBytes"] == 1024
    assert "?" not in evidence.details["url"]
    assert set(evidence.details["criteria_evaluated"]) == {
        "min_bytes", "max_bytes"
    }


async def test_download_canceled_returns_failed_structured_evidence():
    conn = CDPConnection()
    conn._ws = AsyncMock()
    page = PageDomain(conn)

    evidence = await _publish_after_subscribed(
        lambda: page.wait_for_download_complete(timeout=5.0),
        conn,
        [_BEGIN, _progress("canceled")],
    )

    assert evidence.passed is False
    assert evidence.observed == "download canceled"
    assert evidence.details["state"] == "canceled"
    assert evidence.details["filename"] == "report.pdf"


async def test_download_filename_contains_violated_fails():
    conn = CDPConnection()
    conn._ws = AsyncMock()
    page = PageDomain(conn)

    evidence = await _publish_after_subscribed(
        lambda: page.wait_for_download_complete(
            filename_contains="invoice", timeout=5.0
        ),
        conn,
        [_BEGIN, _progress("completed", totalBytes=10)],
    )

    assert evidence.passed is False
    assert "filename criterion violated" in evidence.observed
    assert "invoice" in evidence.observed


async def test_download_min_bytes_violated_fails():
    conn = CDPConnection()
    conn._ws = AsyncMock()
    page = PageDomain(conn)

    evidence = await _publish_after_subscribed(
        lambda: page.wait_for_download_complete(min_bytes=5000, timeout=5.0),
        conn,
        [_BEGIN, _progress("completed", receivedBytes=10, totalBytes=10)],
    )

    assert evidence.passed is False
    assert "min_bytes criterion violated" in evidence.observed


async def test_download_max_bytes_violated_fails():
    conn = CDPConnection()
    conn._ws = AsyncMock()
    page = PageDomain(conn)

    evidence = await _publish_after_subscribed(
        lambda: page.wait_for_download_complete(max_bytes=5, timeout=5.0),
        conn,
        [_BEGIN, _progress("completed", receivedBytes=10, totalBytes=10)],
    )

    assert evidence.passed is False
    assert "max_bytes criterion violated" in evidence.observed


async def test_download_timeout_without_begin_fails_structured():
    conn = CDPConnection()
    conn._ws = AsyncMock()
    page = PageDomain(conn)

    evidence = await page.wait_for_download_complete(timeout=0.05)

    assert evidence.passed is False
    assert evidence.observed == "download did not start before timeout"


async def test_download_bytes_criteria_not_evaluable_when_protocol_omits():
    conn = CDPConnection()
    conn._ws = AsyncMock()
    page = PageDomain(conn)

    evidence = await _publish_after_subscribed(
        lambda: page.wait_for_download_complete(min_bytes=1, max_bytes=9, timeout=5.0),
        conn,
        [_BEGIN, _progress("completed")],
    )

    assert evidence.passed is True
    assert evidence.details.get("bytes_criteria", "").startswith("not_evaluable")
    assert "receivedBytes" not in evidence.details
    assert "totalBytes" not in evidence.details


async def test_enable_downloads_sends_behavior_with_events(tmp_path):
    conn, send = _route_conn({})
    page = PageDomain(conn)
    target_dir = tmp_path / "downloads"
    target_dir.mkdir()

    await page.enable_downloads(target_dir)

    call = next(
        c for c in send.call_args_list if c.args[0] == "Browser.setDownloadBehavior"
    )
    params = call.args[1]
    assert params["behavior"] == "allowAndName"
    assert params["eventsEnabled"] is True
    assert params["downloadPath"] == str(target_dir)


# --- Executor level: declarative actions (_dispatch_action) ----------------


async def test_dispatch_handle_dialog_without_explicit_accept_is_rejected():
    """DoD3: dialogs are NEVER answered by default — accept must be explicit."""

    from src.agent.executor import _dispatch_action

    page = AsyncMock()
    result = await _dispatch_action(
        {"action": "handle_dialog"}, page, AsyncMock(), AsyncMock()
    )

    assert result.startswith("[Error] handle_dialog")
    assert "explicitly" in result
    page.wait_for_dialog.assert_not_called()
    page.handle_dialog.assert_not_called()


async def test_dispatch_wait_for_download_enables_behavior_before_waiting():
    """The action lazily routes downloads so progress events actually flow."""
    from types import SimpleNamespace

    from src.agent.executor import _dispatch_action

    page = AsyncMock()
    evidence = SimpleNamespace(
        passed=True, observed="download completed", details={"filename": "report.pdf"}
    )
    page.wait_for_download_complete = AsyncMock(return_value=evidence)

    result = await _dispatch_action(
        {"action": "wait_for_download", "filename_contains": "report"},
        page,
        AsyncMock(),
        AsyncMock(),
    )

    assert result == "Download completed: report.pdf"
    page.enable_downloads.assert_called_once()
    download_dir = Path(page.enable_downloads.call_args.args[0])
    assert download_dir.is_dir()
    assert download_dir.name.startswith("ready-ai-downloads-")
