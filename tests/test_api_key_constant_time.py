"""Tests for constant-time API key comparison (VAL-SEC-005).

The API key check MUST use hmac.compare_digest instead of the ``in``
operator on a set, which introduces a timing side-channel.
"""
import hmac

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import server
from src.api.server import app, _rate_limiter


@pytest.fixture(autouse=True)
def _clear_rate_limit_store():
    """Reset rate limiter around every test."""
    _rate_limiter._buckets.clear()
    yield
    _rate_limiter._buckets.clear()


@pytest.mark.asyncio
async def test_valid_api_key_authenticates(monkeypatch):
    """A valid API key should pass the auth gate (not 401)."""
    monkeypatch.setattr(server, "_AUTH_DISABLED", False)
    monkeypatch.setattr(server, "_READY_AI_API_KEYS", {"valid-secret-key"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/runs/some-run-id",
            headers={"X-API-Key": "valid-secret-key"},
        )
    # 404 expected (run doesn't exist) — but NOT 401
    assert response.status_code != 401


@pytest.mark.asyncio
async def test_invalid_api_key_rejected(monkeypatch):
    """An invalid API key should be rejected with 401."""
    monkeypatch.setattr(server, "_AUTH_DISABLED", False)
    monkeypatch.setattr(server, "_READY_AI_API_KEYS", {"valid-secret-key"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/runs/some-run-id",
            headers={"X-API-Key": "wrong-key"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_missing_api_key_rejected(monkeypatch):
    """A missing API key should be rejected with 401."""
    monkeypatch.setattr(server, "_AUTH_DISABLED", False)
    monkeypatch.setattr(server, "_READY_AI_API_KEYS", {"valid-secret-key"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/runs/some-run-id")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_hmac_compare_digest_used_for_valid_key(mocker, monkeypatch):
    """hmac.compare_digest MUST be called when validating a valid key."""
    monkeypatch.setattr(server, "_AUTH_DISABLED", False)
    monkeypatch.setattr(server, "_READY_AI_API_KEYS", {"valid-secret-key"})

    mock_compare = mocker.patch("hmac.compare_digest", wraps=hmac.compare_digest)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.get(
            "/runs/some-run-id",
            headers={"X-API-Key": "valid-secret-key"},
        )

    mock_compare.assert_called()


@pytest.mark.asyncio
async def test_hmac_compare_digest_used_for_invalid_key(mocker, monkeypatch):
    """hmac.compare_digest MUST be called even when the key is invalid."""
    monkeypatch.setattr(server, "_AUTH_DISABLED", False)
    monkeypatch.setattr(server, "_READY_AI_API_KEYS", {"valid-secret-key"})

    mock_compare = mocker.patch("hmac.compare_digest", wraps=hmac.compare_digest)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.get(
            "/runs/some-run-id",
            headers={"X-API-Key": "wrong-key"},
        )

    mock_compare.assert_called()
