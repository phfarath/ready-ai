"""Optional remote client for the ready-ai HTTP API (API B parte 2).

``RemoteClient`` is a thin ``aiohttp`` wrapper. Readers return typed
wire models (``remote_models``); invalid shapes raise
``FlowValidationError``. ``get_metrics`` stays a loose mapping (the
metrics file has no schema contract) and ``get_output`` returns raw
bytes (ZIP archive). ``run_flow`` posts a server ``FlowSpec`` built by
``public_flow_to_server_spec`` and returns a typed
``RemoteFlowResult``.

Sessions are opened per request (no lifecycle to manage); the async
context manager exists for ``async with`` ergonomics only. Failures
reuse the public taxonomy: transport problems raise
``ConnectionLostError``, HTTP errors raise ``ReadyAIError``.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Mapping, Optional, TypeVar

import aiohttp
from pydantic import BaseModel, ValidationError

from .exceptions import (
    ConnectionLostError,
    FlowValidationError,
    PolicyViolationError,
    ReadyAIError,
)
from .remote_models import (
    RemoteAggregates,
    RemoteDiff,
    RemoteDocList,
    RemoteDocVersionStatus,
    RemoteExport,
    RemoteFlowResult,
    RemoteHealth,
    RemoteHistory,
    RemoteReadiness,
    RemoteRunList,
    RemoteRunStatus,
)

__all__ = ["RemoteClient", "public_flow_to_server_spec"]


_M = TypeVar("_M", bound=BaseModel)


def _validate(model_cls: type[_M], data: Any, what: str) -> _M:
    """Validate wire JSON into a typed model (fail-closed on drift)."""
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise FlowValidationError(
            f"remote {what}: invalid response shape: {exc}"
        ) from exc


# Public SDK policy (observe/navigate/interactive) -> server FlowSpec
# vocabulary (read/navigate/write). Steps already speak the server
# vocabulary, so only the flow ceiling is translated here.
_POLICY_TO_SERVER = {
    "observe": "read",
    "navigate": "navigate",
    "interactive": "write",
}

#: Flow fields forwarded to POST /flows/run. Everything else —
#: ``version``, ``output`` (a local path), ``timeout_s`` (no server
#: knob) — never travels. Credentials never exist on the public model.
_FLOW_KEYS = frozenset(
    {"name", "url", "steps", "retries", "run_id", "effect_policy"}
)
_STEP_KEYS = frozenset(
    {
        "name",
        "actions",
        "asserts",
        "extract",
        "retries",
        "policy",
        "confirm",
        "irreversible",
        "idempotency_key",
    }
)


def public_flow_to_server_spec(flow: Any, *, headless: bool = True) -> dict[str, Any]:
    """Translate a public ``Flow`` into a server ``FlowSpec`` JSON dict.

    Whitelist-based: only known-safe fields travel. Local profile
    references must have been rejected before this point — a spec
    carries no profile, no paths, no secrets.
    """
    dump = flow.model_dump()
    policy = dump.get("effect_policy", "interactive")
    policy_value = getattr(policy, "value", policy)
    try:
        server_policy = _POLICY_TO_SERVER[str(policy_value)]
    except KeyError as exc:
        raise FlowValidationError(
            f"unsupported effect_policy {policy_value!r} for remote run"
        ) from exc
    spec = {key: dump[key] for key in _FLOW_KEYS if key in dump}
    spec["effect_policy"] = server_policy
    spec["headless"] = headless
    spec["steps"] = [
        {key: step[key] for key in _STEP_KEYS if key in step}
        for step in spec.get("steps", [])
    ]
    return spec


class RemoteClient:
    """Read-only HTTP client for a running ready-ai API server."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: Optional[str] = None,
        session_factory: Optional[Callable[[], Any]] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._headers: dict[str, str] = (
            {"X-API-Key": api_key} if api_key else {}
        )
        self._session_factory = session_factory or aiohttp.ClientSession

    async def __aenter__(self) -> "RemoteClient":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def _request(
        self, method: str, path: str, *, raw: bool = False, **kwargs: Any
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            session = self._session_factory()
            opener = getattr(session, method)
            try:
                async with opener(url, headers=self._headers, **kwargs) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        raise ReadyAIError(
                            f"remote {method.upper()} {path} -> "
                            f"HTTP {resp.status}: {body[:200]}"
                        )
                    if raw:
                        return await resp.read()
                    return await resp.json()
            finally:
                close = getattr(session, "close", None)
                if close is not None:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
        except ReadyAIError:
            raise
        except Exception as exc:
            raise ConnectionLostError(
                f"remote {method.upper()} {path} failed: {exc}"
            ) from exc

    async def _get(self, path: str) -> Any:
        return await self._request("get", path)

    async def health(self) -> RemoteHealth:
        """Liveness probe — ``GET /health`` (public, no auth needed)."""
        return _validate(RemoteHealth, await self._get("/health"), "GET /health")

    async def ready(self) -> RemoteReadiness:
        """Readiness probe — ``GET /ready``."""
        return _validate(RemoteReadiness, await self._get("/ready"), "GET /ready")

    async def get_runs(self) -> RemoteRunList:
        """List documentation runs — ``GET /runs``."""
        return _validate(RemoteRunList, await self._get("/runs"), "GET /runs")

    async def get_run(self, run_id: str) -> RemoteRunStatus:
        """Run status — ``GET /runs/{run_id}``."""
        return _validate(
            RemoteRunStatus, await self._get(f"/runs/{run_id}"), f"GET /runs/{run_id}"
        )

    async def get_output(self, run_id: str) -> bytes:
        """Run output ZIP archive — ``GET /runs/{run_id}/output``."""
        data = await self._request("get", f"/runs/{run_id}/output", raw=True)
        if not isinstance(data, bytes):
            raise FlowValidationError(
                f"remote GET /runs/{run_id}/output: expected bytes, "
                f"got {type(data).__name__}"
            )
        return data

    async def get_metrics(self, run_id: str) -> Mapping[str, Any]:
        """Run metrics file — ``GET /runs/{run_id}/metrics`` (loose shape)."""
        data = await self._get(f"/runs/{run_id}/metrics")
        if not isinstance(data, dict):
            raise FlowValidationError(
                f"remote GET /runs/{run_id}/metrics: expected object, "
                f"got {type(data).__name__}"
            )
        return data

    async def export_run(self, run_id: str) -> RemoteExport:
        """Export a run — ``POST /runs/{run_id}/export`` (read-only effect)."""
        return _validate(
            RemoteExport,
            await self._request("post", f"/runs/{run_id}/export"),
            f"POST /runs/{run_id}/export",
        )

    async def diff_run(self, run_id: str) -> RemoteDiff:
        """Textual diff vs previous version — ``GET /runs/{run_id}/diff``."""
        return _validate(
            RemoteDiff, await self._get(f"/runs/{run_id}/diff"), f"GET /runs/{run_id}/diff"
        )

    async def list_doc_sets(self) -> RemoteDocList:
        """Generated documentation sets — ``GET /doc-sets``."""
        return _validate(RemoteDocList, await self._get("/doc-sets"), "GET /doc-sets")

    async def doc_status(self, version: str) -> RemoteDocVersionStatus:
        """Docs health for a version — ``GET /docs/{version}/status``."""
        return _validate(
            RemoteDocVersionStatus,
            await self._get(f"/docs/{version}/status"),
            f"GET /docs/{version}/status",
        )

    async def get_history(self) -> RemoteHistory:
        """Run history — ``GET /history``."""
        return _validate(RemoteHistory, await self._get("/history"), "GET /history")

    async def get_aggregates(self) -> RemoteAggregates:
        """History aggregates — ``GET /history/aggregates``."""
        return _validate(
            RemoteAggregates, await self._get("/history/aggregates"), "GET /history/aggregates"
        )

    async def run_flow(self, spec: dict[str, Any]) -> RemoteFlowResult:
        """Execute a flow server-side — ``POST /flows/run``."""
        return _validate(
            RemoteFlowResult,
            await self._request("post", "/flows/run", json=spec),
            "POST /flows/run",
        )


def _require_remote_capable(*, profile: Optional[str], confirm: Any) -> None:
    """Fail closed before any HTTP when the remote run cannot be honored."""
    if profile is not None:
        raise PolicyViolationError(
            "remote run cannot use a local profile: server-side Chrome "
            "has no access to this machine's allowlisted profiles "
            f"(got {profile!r}); pass no profile or run locally"
        )
    if confirm:
        raise PolicyViolationError(
            "remote run cannot honor confirm keys: the server executes "
            "without the caller's confirmation channel; run locally instead"
        )
