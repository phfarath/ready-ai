"""Security tests: previous_run_id query parameter validation prevents path traversal.

Validates that the ``previous_run_id`` query parameter on the
``GET /runs/{run_id}/diff`` endpoint is validated against
``^[A-Za-z0-9_-]+$`` before being interpolated into filesystem paths.

Malicious values (containing ``.``, ``/``, ``;``, etc.) MUST return HTTP 422,
while valid alphanumeric/underscore/hyphen values MUST be accepted (not 422).
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


# A valid run_id used in the path component (always passes path validation)
VALID_PATH_RUN_ID = "valid_run_123"

# ─── Valid previous_run_id patterns ───────────────────────────────────────

VALID_PREVIOUS_RUN_IDS = [
    "valid_prev_id",
    "valid-prev-id",
    "prev_001",
    "PREV",
    "a",
    "1",
    "a1B2c3_-",
]

# ─── Malicious previous_run_id query values ───────────────────────────────
# These contain characters outside ^[A-Za-z0-9_-]+$ that could enable path
# traversal or injection when interpolated into Path(f"./output/{previous_run_id}").

MALICIOUS_PREVIOUS_RUN_IDS = [
    "../../etc/passwd",    # path traversal
    "..\\..\\windows",     # windows path traversal
    ".env",                # dot-prefixed hidden file
    "foo.bar",             # contains dot
    "foo/bar",             # contains slash
    "foo;bar",             # semicolon injection
    "foo@bar",             # at-sign injection
    "foo$bar",             # dollar sign
    "foo!bar",             # exclamation
    "foo~bar",             # tilde
]


@pytest.mark.asyncio
async def test_malicious_previous_run_id_rejected():
    """GET /runs/{run_id}/diff?previous_run_id=<malicious> must return 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for malicious_id in MALICIOUS_PREVIOUS_RUN_IDS:
            _rate_limiter._buckets.clear()
            resp = await client.get(
                f"/runs/{VALID_PATH_RUN_ID}/diff",
                params={"previous_run_id": malicious_id},
            )
            assert resp.status_code == 422, (
                f"Malicious previous_run_id '{malicious_id}' was NOT rejected with 422 "
                f"on GET /runs/{{run_id}}/diff: got {resp.status_code}"
            )


@pytest.mark.asyncio
async def test_valid_previous_run_id_accepted():
    """Valid previous_run_id query values must NOT return 422.

    They may return 404 (run not found) or 200 (success), but never 422.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for valid_id in VALID_PREVIOUS_RUN_IDS:
            _rate_limiter._buckets.clear()
            resp = await client.get(
                f"/runs/{VALID_PATH_RUN_ID}/diff",
                params={"previous_run_id": valid_id},
            )
            assert resp.status_code != 422, (
                f"Valid previous_run_id '{valid_id}' was rejected with 422 "
                f"on GET /runs/{{run_id}}/diff: {resp.text}"
            )


@pytest.mark.asyncio
async def test_no_previous_run_id_still_works():
    """GET /runs/{run_id}/diff without previous_run_id must NOT return 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        _rate_limiter._buckets.clear()
        resp = await client.get(f"/runs/{VALID_PATH_RUN_ID}/diff")
        assert resp.status_code != 422, (
            f"Missing previous_run_id was rejected with 422: {resp.text}"
        )
