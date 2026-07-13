"""
Tests for transient exception retry in LLM client (VAL-ROB-004).

_call_with_retry must retry on transient transport exceptions:
  Timeout, APIConnectionError, InternalServerError, ServiceUnavailableError
(and the existing RateLimitError).

It must NOT retry non-transient exceptions:
  AuthenticationError, BadRequestError, NotFoundError
These must be re-raised immediately after a single call.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm.client import MAX_LLM_RETRIES, LLMClient  # noqa: E402
from src.llm import client as _client_mod  # noqa: E402

# IMPORTANT: reference exception classes through the litellm module that
# client.py captured at its own import time. Another test module
# (test_api_batch) replaces sys.modules["litellm"] with a MagicMock at
# collection time, so a bare `import litellm.exceptions` would yield mock
# objects. client.py is imported early (via src.agent.planner in
# test_agent_core, collected before test_api_batch), so its `litellm`
# binding is the real module.
_exc = _client_mod.litellm.exceptions


def _make_exc(cls):
    """Build a real litellm exception instance."""
    return cls("boom", model="test-model", llm_provider="openai")


def _success_response(content: str = "ok") -> MagicMock:
    """A minimal litellm/openai-style completion response mock."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage = None
    return resp


@pytest.fixture(autouse=True)
def _no_sleep(mocker):
    """Avoid real asyncio.sleep delays during retry backoff."""
    mocker.patch("src.llm.client.asyncio.sleep", new_callable=AsyncMock)


@pytest.fixture(autouse=True)
def _no_metrics(mocker):
    """get_metrics() returns None in test (no RunContext active)."""
    mocker.patch("src.llm.client.get_metrics", return_value=None)


class TestTransientRetriedThenSuccess:
    """Transient exceptions raised twice then success must be retried."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc_cls", [
        _exc.Timeout,
        _exc.APIConnectionError,
        _exc.InternalServerError,
        _exc.ServiceUnavailableError,
    ])
    async def test_transient_retried_then_success(self, mocker, exc_cls):
        client = LLMClient()
        mock_acompletion = mocker.patch(
            "src.llm.client.litellm.acompletion",
            new_callable=AsyncMock,
            side_effect=[_make_exc(exc_cls), _make_exc(exc_cls), _success_response("done")],
        )

        result = await client._call_with_retry({"model": "test"}, role="critic")

        assert result == "done"
        # Three attempts: two failures + one success
        assert mock_acompletion.await_count == 3

    @pytest.mark.asyncio
    async def test_ratelimit_still_retried(self, mocker):
        """RateLimitError (the original retried exception) still retries."""
        client = LLMClient()
        err = _make_exc(_exc.RateLimitError)
        mock_acompletion = mocker.patch(
            "src.llm.client.litellm.acompletion",
            new_callable=AsyncMock,
            side_effect=[err, err, _success_response("recovered")],
        )

        result = await client._call_with_retry({"model": "test"}, role="critic")

        assert result == "recovered"
        assert mock_acompletion.await_count == 3


class TestNonTransientNotRetried:
    """Non-transient exceptions must NOT be retried; re-raised immediately."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc_cls", [
        _exc.AuthenticationError,
        _exc.BadRequestError,
        _exc.NotFoundError,
    ])
    async def test_non_transient_not_retried(self, mocker, exc_cls):
        client = LLMClient()
        mock_acompletion = mocker.patch(
            "src.llm.client.litellm.acompletion",
            new_callable=AsyncMock,
            side_effect=_make_exc(exc_cls),
        )

        with pytest.raises(exc_cls):
            await client._call_with_retry({"model": "test"}, role="critic")

        # Exactly one call — never retried
        assert mock_acompletion.await_count == 1


class TestRetriesExhausted:
    """When all retries are exhausted on a transient, the last error is raised."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc_cls", [
        _exc.Timeout,
        _exc.APIConnectionError,
        _exc.InternalServerError,
        _exc.ServiceUnavailableError,
        _exc.RateLimitError,
    ])
    async def test_all_retries_exhausted(self, mocker, exc_cls):
        client = LLMClient()
        err = _make_exc(exc_cls)
        mock_acompletion = mocker.patch(
            "src.llm.client.litellm.acompletion",
            new_callable=AsyncMock,
            side_effect=err,
        )

        with pytest.raises(exc_cls):
            await client._call_with_retry({"model": "test"}, role="critic")

        # Retried up to MAX_LLM_RETRIES times
        assert mock_acompletion.await_count == MAX_LLM_RETRIES
