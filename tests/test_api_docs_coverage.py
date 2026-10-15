"""API reference coverage: every live route must be documented in docs/API.md.

Regression guard (READY-AI-T-API-DOCS): new endpoints must come with
docs, and query params must match. Swagger-internal routes
(/openapi.json, /docs, /redoc, oauth2-redirect) are excluded.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.api.server import app

_DOC_PATH = Path(__file__).parent.parent / "docs" / "API.md"

_EXCLUDED_PATHS = frozenset(
    {"/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"}
)


def _live_routes() -> list[tuple[str, str, list[str]]]:
    routes: list[tuple[str, str, list[str]]] = []
    for route in app.routes:
        methods = sorted(getattr(route, "methods", set()) or set())
        path = getattr(route, "path", "")
        if path in _EXCLUDED_PATHS:
            continue
        dependant = getattr(route, "dependant", None)
        qparams = [
            getattr(p, "name", "")
            for p in (getattr(dependant, "query_params", None) or [])
        ]
        for method in methods:
            if method in ("OPTIONS", "HEAD"):
                continue
            if not methods or (len(methods) == 1 and "HEAD" in methods):
                continue
            routes.append((method, path, [q for q in qparams if q]))
    return routes


def test_every_route_documented():
    doc = _DOC_PATH.read_text(encoding="utf-8")
    missing = [
        f"{method} {path}"
        for method, path, _ in _live_routes()
        if f"`{method} {path}`" not in doc
    ]
    assert not missing, f"undocumented routes: {missing}"


def test_every_query_param_documented():
    doc = _DOC_PATH.read_text(encoding="utf-8")
    missing = [
        f"{method} {path}: `{param}`"
        for method, path, params in _live_routes()
        for param in params
        if param not in doc
    ]
    assert not missing, f"undocumented query params: {missing}"
