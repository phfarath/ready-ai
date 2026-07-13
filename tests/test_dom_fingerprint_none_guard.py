"""
Tests for the get_metrics() None guard in dom_fingerprint().

VAL-ROB-003: dom_fingerprint() must guard get_metrics() returning None
before calling .increment(). When no RunContext is active, get_metrics()
returns None and the old code raised AttributeError inside the except
block, masking the intended __fp_error__ sentinel return.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.dom_utils import FP_ERROR_PREFIX, dom_fingerprint


@pytest.mark.asyncio
async def test_failure_does_not_crash_when_metrics_none(mocker):
    """When get_metrics() returns None (no RunContext active),
    dom_fingerprint returns the __fp_error__ sentinel without raising
    AttributeError."""
    mocker.patch("src.agent.dom_utils.get_metrics", return_value=None)
    runtime = AsyncMock()
    runtime.evaluate = AsyncMock(side_effect=RuntimeError("ws closed"))
    fp = await dom_fingerprint(runtime)
    assert fp.startswith(FP_ERROR_PREFIX)


@pytest.mark.asyncio
async def test_failure_increments_metric_when_available(mocker):
    """When get_metrics() returns a real metrics object, metrics.increment
    IS called on failure (no regression on the happy metrics path)."""
    fake_metrics = MagicMock()
    mocker.patch("src.agent.dom_utils.get_metrics", return_value=fake_metrics)
    runtime = AsyncMock()
    runtime.evaluate = AsyncMock(side_effect=RuntimeError("boom"))
    await dom_fingerprint(runtime)
    fake_metrics.increment.assert_called_once_with(
        "fingerprint.errors", source="cdp"
    )
