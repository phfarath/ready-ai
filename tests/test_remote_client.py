"""API expansion B: ReadyAI remoto opcional (base_url) — read-only.

TDD RED: ``RemoteClient`` fino sobre aiohttp + guards fail-closed no
modo remoto. Modo local (sem ``base_url``) intacto.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ready_ai import Flow, FlowStep, ReadyAI
from ready_ai.exceptions import (
    ConnectionLostError,
    PolicyViolationError,
    ReadyAIError,
)
from ready_ai.remote import RemoteClient


class _FakeResponse:
    def __init__(
        self,
        status: int,
        payload: Any,
        raise_json: Optional[Exception] = None,
    ):
        self.status = status
        self._payload = payload
        self._raise_json = raise_json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        if self._raise_json is not None:
            raise self._raise_json
        return self._payload

    async def text(self):
        return str(self._payload)


class _FakeSession:
    """Fake de ``aiohttp.ClientSession``: registra chamadas, serve respostas."""

    def __init__(self, response: _FakeResponse, exc: Optional[Exception] = None):
        self._response = response
        self._exc = exc
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def _request(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if self._exc is not None:
            raise self._exc
        return self._response

    def get(self, url: str, **kwargs):
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self._request("POST", url, **kwargs)

    async def close(self):
        self.closed = True


def _client(payload: Any = None, status: int = 200, **kwargs) -> tuple[RemoteClient, _FakeSession]:
    session = _FakeSession(_FakeResponse(status, payload if payload is not None else {}))
    factory = MagicMock(return_value=session)
    client = RemoteClient("http://api:8000/", session_factory=factory, **kwargs)
    return client, session


def test_local_mode_has_no_remote():
    ai = ReadyAI()
    assert ai._remote is None


def test_base_url_normalized_and_client_created():
    ai = ReadyAI(base_url="http://api:8000/")
    assert ai._remote is not None
    assert ai._remote.base_url == "http://api:8000"


def _dummy_flow() -> Flow:
    return Flow(url="https://example.com", steps=[FlowStep(name="x", actions=[])])


@pytest.mark.parametrize("method", ["run", "start", "resume", "replay_manifest"])
async def test_execution_methods_fail_closed_in_remote_mode(method):
    ai = ReadyAI(base_url="http://api:8000")
    with pytest.raises(PolicyViolationError):
        if method == "run":
            await ai.run(_dummy_flow())
        elif method == "start":
            ai.start(_dummy_flow())
        elif method == "resume":
            await ai.resume(_dummy_flow(), run_id="run-1")
        else:
            await ai.replay_manifest({"manifest_version": "x"})


async def test_run_flow_delegates_to_run_guard():
    ai = ReadyAI(base_url="http://api:8000")

    with pytest.raises(PolicyViolationError):
        await ai.run_flow(_dummy_flow())


async def test_get_json_returns_payload():
    client, session = _client({"runs": []})
    assert await client.get_runs() == {"runs": []}
    assert session.calls[0]["url"] == "http://api:8000/runs"
    assert session.closed


async def test_get_run_by_id():
    client, session = _client({"run_id": "r1"})
    assert await client.get_run("r1") == {"run_id": "r1"}
    assert session.calls[0]["url"] == "http://api:8000/runs/r1"


async def test_http_error_maps_to_ready_ai_error():
    client, _ = _client({"detail": "nope"}, status=404)
    with pytest.raises(ReadyAIError) as excinfo:
        await client.get_run("missing")
    assert "404" in str(excinfo.value)


async def test_connection_failure_maps_to_connection_lost():
    session = _FakeSession(
        _FakeResponse(200, {}), exc=ConnectionError("refused")
    )
    factory = MagicMock(return_value=session)
    client = RemoteClient("http://api:8000", session_factory=factory)
    with pytest.raises(ConnectionLostError):
        await client.health()


async def test_api_key_sent_as_header():
    client, session = _client({"status": "healthy"}, api_key="secret")
    await client.health()
    assert session.calls[0]["headers"]["X-API-Key"] == "secret"


async def test_no_api_key_no_header():
    client, session = _client({"status": "healthy"})
    await client.health()
    assert "X-API-Key" not in session.calls[0].get("headers", {})


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_live_health_against_real_server():
    """Prova viva barata: RemoteClient fala com o FastAPI real.

    Roda num subprocesso com interpretador limpo porque a suite
    completa polui ``sys.modules['aiohttp']`` (test_api_batch); aqui
    o client usa o aiohttp de verdade contra o server real.
    """
    import subprocess
    import uvicorn

    from src.api.server import app

    port = _free_port()
    repo_root = str(Path(__file__).parent.parent)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            lifespan="off",  # lifespan registra signal handlers (main thread)
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    probe = "\n".join(
        [
            "import asyncio, sys",
            f"sys.path.insert(0, {repo_root!r})",
            "from ready_ai.remote import RemoteClient",
            "async def main():",
            "    async with RemoteClient(sys.argv[1]) as c:",
            "        r = await c.health()",
            "        assert r['status'] == 'healthy', r",
            "        assert r['service'] == 'ready-ai', r",
            "        print('LIVE_OK')",
            "asyncio.run(main())",
        ]
    )
    try:
        deadline = time.time() + 15
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started, "uvicorn nao subiu a tempo"

        proc = subprocess.run(
            [sys.executable, "-c", probe, f"http://127.0.0.1:{port}"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=repo_root,
        )
        assert "LIVE_OK" in proc.stdout, proc.stderr[-2000:]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
