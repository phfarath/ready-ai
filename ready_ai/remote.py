"""Optional remote client for the ready-ai HTTP API (read-only).

``RemoteClient`` is a thin ``aiohttp`` wrapper over the API's read-only
routes. It is owned by ``ReadyAI`` when constructed with ``base_url``;
use it directly for health/history/status polling without the engine::

    async with RemoteClient("http://127.0.0.1:8000") as client:
        print(await client.health())

Sessions are opened per request (no lifecycle to manage); the async
context manager exists for ``async with`` ergonomics only. Failures
reuse the public taxonomy: transport problems raise
``ConnectionLostError``, HTTP errors raise ``ReadyAIError``.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Mapping, Optional

import aiohttp

from .exceptions import ConnectionLostError, ReadyAIError

__all__ = ["RemoteClient"]


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
        self, method: str, path: str, **kwargs: Any
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

    async def _get_object(self, path: str) -> dict[str, Any]:
        """GET que garante objeto JSON (as rotas cobertas devolvem dicts)."""
        data = await self._get(path)
        if not isinstance(data, dict):
            raise ReadyAIError(
                f"remote GET {path} -> resposta inesperada "
                f"(esperado objeto JSON, veio {type(data).__name__})"
            )
        return data

    async def health(self) -> Mapping[str, Any]:
        """Liveness probe — ``GET /health`` (public, no auth needed)."""
        return await self._get_object("/health")

    async def ready(self) -> Mapping[str, Any]:
        """Readiness probe — ``GET /ready``."""
        return await self._get_object("/ready")

    async def get_runs(self) -> Mapping[str, Any]:
        """List documentation runs — ``GET /runs``."""
        return await self._get_object("/runs")

    async def get_run(self, run_id: str) -> Mapping[str, Any]:
        """Run status — ``GET /runs/{run_id}``."""
        return await self._get_object(f"/runs/{run_id}")

    async def get_output(self, run_id: str) -> Mapping[str, Any]:
        """Run output artifacts — ``GET /runs/{run_id}/output``."""
        return await self._get_object(f"/runs/{run_id}/output")

    async def get_metrics(self, run_id: str) -> Mapping[str, Any]:
        """Run metrics — ``GET /runs/{run_id}/metrics``."""
        return await self._get_object(f"/runs/{run_id}/metrics")

    async def export_run(self, run_id: str) -> Mapping[str, Any]:
        """Export a run — ``POST /runs/{run_id}/export`` (read-only effect)."""
        data = await self._request("post", f"/runs/{run_id}/export")
        if not isinstance(data, dict):
            raise ReadyAIError(
                f"remote POST /runs/{run_id}/export -> resposta inesperada "
                f"(esperado objeto JSON, veio {type(data).__name__})"
            )
        return data

    async def diff_run(self, run_id: str) -> Mapping[str, Any]:
        """Textual diff vs previous version — ``GET /runs/{run_id}/diff``."""
        return await self._get_object(f"/runs/{run_id}/diff")

    async def list_doc_sets(self) -> Mapping[str, Any]:
        """Generated documentation sets — ``GET /doc-sets``."""
        return await self._get_object("/doc-sets")

    async def doc_status(self, version: str) -> Mapping[str, Any]:
        """Docs health for a version — ``GET /docs/{version}/status``."""
        return await self._get_object(f"/docs/{version}/status")

    async def get_history(self) -> Mapping[str, Any]:
        """Run history — ``GET /history``."""
        return await self._get_object("/history")

    async def get_aggregates(self) -> Mapping[str, Any]:
        """History aggregates — ``GET /history/aggregates``."""
        return await self._get_object("/history/aggregates")
