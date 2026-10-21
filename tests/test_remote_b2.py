"""API B parte 2: modelos tipados + run remoto — TDD RED."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from ready_ai import BrowserOptions, Flow, FlowStep, ReadyAI
from ready_ai.exceptions import FlowValidationError, PolicyViolationError


class _FakeResponse:
    def __init__(self, status=200, payload=None, raw_bytes=None):
        self.status = status
        self._payload = payload if payload is not None else {}
        self._raw = raw_bytes

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)

    async def read(self):
        return self._raw if self._raw is not None else b""


class _FakeSession:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    def _request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._response

    def get(self, url, **kwargs):
        return self._request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._request("POST", url, **kwargs)

    async def close(self):
        return None


def _remote(response, **kwargs):
    from ready_ai.remote import RemoteClient

    session = _FakeSession(response)
    factory = MagicMock(return_value=session)
    return RemoteClient("http://api:8000", session_factory=factory, **kwargs), session


def _flow():
    from ready_ai import FlowAction

    return Flow(
        url="https://example.com",
        steps=[
            FlowStep(
                name="open",
                actions=[FlowAction(action="navigate", url="https://example.com")],
            )
        ],
    )


async def test_health_typed():
    from ready_ai.remote_models import RemoteHealth

    client, _ = _remote(
        _FakeResponse(200, {"status": "healthy", "service": "ready-ai"})
    )
    health = await client.health()
    assert isinstance(health, RemoteHealth)
    assert health.status == "healthy"


async def test_run_status_typed():
    from ready_ai.remote_models import RemoteRunStatus

    client, _ = _remote(
        _FakeResponse(200, {"run_id": "r1", "status": "FINISHED", "goal": "g",
                            "url": "u", "executed_steps": 1,
                            "total_planned_steps": 1, "last_known_url": None})
    )
    status = await client.get_run("r1")
    assert isinstance(status, RemoteRunStatus)
    assert status.run_id == "r1"


async def test_invalid_shape_raises_validation():
    client, _ = _remote(_FakeResponse(200, {"nonsense": True}))
    try:
        await client.get_run("r1")
    except FlowValidationError:
        return
    raise AssertionError("invalid shape should raise FlowValidationError")


async def test_metrics_stays_mapping():
    client, _ = _remote(_FakeResponse(200, {"llm_calls": 3, "custom": [1]}))
    metrics = await client.get_metrics("r1")
    assert metrics == {"llm_calls": 3, "custom": [1]}


async def test_output_returns_bytes():
    client, _ = _remote(_FakeResponse(200, raw_bytes=b"PK\x03\x04zip"))
    data = await client.get_output("r1")
    assert isinstance(data, bytes)
    assert data.startswith(b"PK")


async def test_run_flow_remote_posts_whitelisted_spec():
    from ready_ai import RunResult

    session = _FakeSession(
        _FakeResponse(200, {"run_id": "r9", "status": "passed", "url": "https://example.com",
                            "steps": [{"index": 0, "name": "open", "status": "passed"}]})
    )
    ai = ReadyAI(base_url="http://api:8000")
    ai.remote._session_factory = MagicMock(return_value=session)
    result = await ai.run(_flow())
    assert isinstance(result, RunResult)
    assert result.status == "passed"
    post = next(c for c in session.calls if c["method"] == "POST")
    assert post["url"] == "http://api:8000/flows/run"
    body = post["json"]
    assert set(body) <= {"name", "url", "steps", "retries", "run_id", "effect_policy", "headless"}
    assert body["effect_policy"] == "write"
    assert "cookies_file" not in body
    assert "output" not in body
    assert "version" not in body


async def test_run_flow_refuses_profile():
    ai = ReadyAI(base_url="http://api:8000")
    with _no_http(ai):
        try:
            await ai.run(_flow(), browser=BrowserOptions(profile="qa"))
        except PolicyViolationError:
            return
    raise AssertionError("profile remoto deveria falhar")


async def test_run_flow_refuses_confirm():
    ai = ReadyAI(base_url="http://api:8000")
    with _no_http(ai):
        try:
            await ai.run(_flow(), confirm=["run-1:step-1"])
        except PolicyViolationError:
            return
    raise AssertionError("confirm remoto deveria falhar")


class _no_http:
    """Garante que nenhum HTTP aconteceu (factory que explode se chamada)."""

    def __init__(self, ai):
        self.ai = ai

    def __enter__(self):
        def _boom():
            raise AssertionError("HTTP nao deveria acontecer")

        self.ai.remote._session_factory = _boom
        return self

    def __exit__(self, *args):
        return False
