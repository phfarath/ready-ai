"""Security tests: API run_id path parameter validation prevents path traversal.

Validates VAL-SEC-004: Any API endpoint accepting run_id as a path parameter
MUST validate it matches ^[A-Za-z0-9_-]+$. Requests with path traversal
characters MUST return HTTP 422.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.server import app, _rate_limiter


@pytest.fixture(autouse=True)
def clear_rate_limit_store():
    """Reset rate limiter before each test to avoid cross-test interference."""
    _rate_limiter._buckets.clear()
    yield
    _rate_limiter._buckets.clear()


# ─── Valid run_id patterns ────────────────────────────────────────────────

VALID_RUN_IDS = [
    "valid_run_id",
    "valid-run-id",
    "valid_run_id_123",
    "VALID_RUN_ID",
    "a",
    "A",
    "1",
    "a1B2c3_-",
]

# ─── Malicious single-segment run_id values ───────────────────────────────
# These values are single URL path segments (no literal /) that contain
# characters outside ^[A-Za-z0-9_-]+$.  Without pattern validation they
# reach the endpoint and are used in filesystem path construction.
# Note: literal "." and ".." segments are normalized away by HTTP clients
# per RFC 3986, so they never reach the server — we test dot-PREFIXED
# values instead (e.g. ".env") which DO reach the endpoint.

MALICIOUS_RUN_IDS = [
    ".env",         # dot-prefixed hidden config file
    ".gitignore",   # dot-prefixed hidden file
    ".htaccess",    # dot-prefixed server config
    "foo.bar",      # contains dot
    "foo;bar",      # semicolon injection
    "foo@bar",      # at-sign injection
    "foo~bar",      # tilde
    "foo$bar",      # dollar sign
    "foo!bar",      # exclamation
    "foo=bar",      # equals sign
]

# ─── Endpoints to test ────────────────────────────────────────────────────

RUN_ENDPOINTS = [
    ("GET", "/runs/{run_id}"),
    ("GET", "/runs/{run_id}/output"),
    ("GET", "/runs/{run_id}/metrics"),
    ("GET", "/runs/{run_id}/diff"),
    ("POST", "/runs/{run_id}/export"),
]


@pytest.mark.asyncio
async def test_valid_run_ids_accepted():
    """Valid run_id patterns must NOT return 422 on any endpoint.

    They may return 404 (run not found) or 200 (success), but never 422.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for valid_id in VALID_RUN_IDS:
            _rate_limiter._buckets.clear()
            # Test GET /runs/{run_id} — the simplest endpoint
            resp = await client.get(f"/runs/{valid_id}")
            assert resp.status_code != 422, (
                f"Valid run_id '{valid_id}' was rejected with 422 on GET /runs/{{run_id}}: {resp.text}"
            )


@pytest.mark.asyncio
async def test_malicious_run_ids_rejected_get_status():
    """GET /runs/{run_id} must return 422 for path traversal / special chars."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for malicious_id in MALICIOUS_RUN_IDS:
            _rate_limiter._buckets.clear()
            resp = await client.get(f"/runs/{malicious_id}")
            assert resp.status_code == 422, (
                f"Malicious run_id '{malicious_id}' was NOT rejected with 422 on "
                f"GET /runs/{{run_id}}: got {resp.status_code}"
            )


@pytest.mark.asyncio
async def test_malicious_run_ids_rejected_get_output():
    """GET /runs/{run_id}/output must return 422 for path traversal / special chars."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for malicious_id in MALICIOUS_RUN_IDS:
            _rate_limiter._buckets.clear()
            resp = await client.get(f"/runs/{malicious_id}/output")
            assert resp.status_code == 422, (
                f"Malicious run_id '{malicious_id}' was NOT rejected with 422 on "
                f"GET /runs/{{run_id}}/output: got {resp.status_code}"
            )


@pytest.mark.asyncio
async def test_malicious_run_ids_rejected_get_metrics():
    """GET /runs/{run_id}/metrics must return 422 for path traversal / special chars."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for malicious_id in MALICIOUS_RUN_IDS:
            _rate_limiter._buckets.clear()
            resp = await client.get(f"/runs/{malicious_id}/metrics")
            assert resp.status_code == 422, (
                f"Malicious run_id '{malicious_id}' was NOT rejected with 422 on "
                f"GET /runs/{{run_id}}/metrics: got {resp.status_code}"
            )


@pytest.mark.asyncio
async def test_malicious_run_ids_rejected_get_diff():
    """GET /runs/{run_id}/diff must return 422 for path traversal / special chars."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for malicious_id in MALICIOUS_RUN_IDS:
            _rate_limiter._buckets.clear()
            resp = await client.get(f"/runs/{malicious_id}/diff")
            assert resp.status_code == 422, (
                f"Malicious run_id '{malicious_id}' was NOT rejected with 422 on "
                f"GET /runs/{{run_id}}/diff: got {resp.status_code}"
            )


@pytest.mark.asyncio
async def test_malicious_run_ids_rejected_post_export():
    """POST /runs/{run_id}/export must return 422 for path traversal / special chars."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for malicious_id in MALICIOUS_RUN_IDS:
            _rate_limiter._buckets.clear()
            resp = await client.post(
                f"/runs/{malicious_id}/export",
                json={"format": "markdown"},
            )
            assert resp.status_code == 422, (
                f"Malicious run_id '{malicious_id}' was NOT rejected with 422 on "
                f"POST /runs/{{run_id}}/export: got {resp.status_code}"
            )


@pytest.mark.asyncio
async def test_url_encoded_path_traversal_no_file_content():
    """URL-encoded path traversal must not return 200 or file content.

    Uses %2F (encoded slash) and %5C (encoded backslash) variants.
    These may return 404 (route doesn't match after decoding) or 422
    (pattern validation), but NEVER 200 with file content.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        encoded_traversals = [
            "/runs/..%2F..%2Fetc%2Fpasswd",
            "/runs/..%2F..%2Fetc%2Fpasswd/output",
            "/runs/..%2F..%2Fetc%2Fpasswd/metrics",
            "/runs/..%5C..%5Cwindows%5Csystem32",
        ]
        for url in encoded_traversals:
            _rate_limiter._buckets.clear()
            resp = await client.get(url)
            assert resp.status_code != 200, (
                f"URL-encoded traversal '{url}' returned 200 — possible file content leak!"
            )
            # Should be 404 (route mismatch) or 422 (validation), never 200 or 500
            assert resp.status_code in (404, 422, 429), (
                f"URL-encoded traversal '{url}' returned unexpected {resp.status_code}: {resp.text[:200]}"
            )


@pytest.mark.asyncio
async def test_literal_path_traversal_no_file_content():
    """Literal path traversal (../) must not return 200 or file content.

    HTTP clients normalize ../ in URLs per RFC 3986, so the request
    typically doesn't reach the intended route.  The key requirement is
    that no system file content is returned.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        literal_traversals = [
            "/runs/../../etc/passwd",
            "/runs/../../etc/passwd/output",
            "/runs/../../etc/shadow",
        ]
        for url in literal_traversals:
            _rate_limiter._buckets.clear()
            resp = await client.get(url)
            assert resp.status_code != 200, (
                f"Literal traversal '{url}' returned 200 — possible file content leak!"
            )
            # Body must not contain typical /etc/passwd content
            body = resp.text.lower()
            assert "root:" not in body, (
                f"Literal traversal '{url}' returned /etc/passwd content!"
            )


@pytest.mark.asyncio
async def test_all_endpoints_consistent_validation():
    """All five endpoints with run_id must consistently reject the same malicious value."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Use ".env" as the canonical malicious value (dot-prefixed hidden file)
        malicious_id = ".env"

        endpoints = [
            ("GET", f"/runs/{malicious_id}"),
            ("GET", f"/runs/{malicious_id}/output"),
            ("GET", f"/runs/{malicious_id}/metrics"),
            ("GET", f"/runs/{malicious_id}/diff"),
            ("POST", f"/runs/{malicious_id}/export"),
        ]

        for method, url in endpoints:
            _rate_limiter._buckets.clear()
            if method == "GET":
                resp = await client.get(url)
            else:
                resp = await client.post(url, json={"format": "markdown"})
            assert resp.status_code == 422, (
                f"Endpoint {method} {url} did not reject malicious run_id with 422: "
                f"got {resp.status_code}"
            )
