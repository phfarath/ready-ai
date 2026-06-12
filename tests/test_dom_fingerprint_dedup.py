"""
Regression tests for the deduplicated DOM fingerprint utility.

P0-2 of the CDP resilience roadmap: ensure that there is exactly one
implementation of `dom_fingerprint`, that it emits the FP_ERROR_PREFIX
sentinel on failure (so two consecutive failures never compare equal),
and that the recovery module re-exports the canonical one.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent import dom_utils, recovery
from src.agent.dom_utils import FP_ERROR_PREFIX, dom_fingerprint
from src.observability import init_run_context


@pytest.fixture(autouse=True)
def _run_context():
    """Each test gets its own isolated metrics run context."""
    init_run_context("test-dedup")
    yield


class TestDeduplication:
    def test_recovery_reexports_canonical(self):
        # Same callable object — not a copy.
        assert recovery.dom_fingerprint is dom_fingerprint
        assert recovery.dom_fingerprint is dom_utils.dom_fingerprint


class TestFingerprintSentinel:
    @pytest.mark.asyncio
    async def test_failure_emits_sentinel(self):
        runtime = AsyncMock()
        runtime.evaluate = AsyncMock(side_effect=RuntimeError("ws closed"))
        fp1 = await dom_fingerprint(runtime)
        fp2 = await dom_fingerprint(runtime)
        assert fp1.startswith(FP_ERROR_PREFIX)
        assert fp2.startswith(FP_ERROR_PREFIX)
        # Two consecutive failures must compare unequal — this is the
        # whole point of the sentinel vs the old "return empty string"
        # behavior that masked CDP flakiness as "no drift".
        assert fp1 != fp2

    @pytest.mark.asyncio
    async def test_success_does_not_emit_sentinel(self):
        runtime = AsyncMock()
        runtime.evaluate = AsyncMock(return_value="button|Save|")
        fp = await dom_fingerprint(runtime)
        assert not fp.startswith(FP_ERROR_PREFIX)
        # MD5 of the joined payload — stable for the same input.
        assert len(fp) == 32

    @pytest.mark.asyncio
    async def test_failure_increments_metric(self):
        from src.observability import get_metrics

        metrics = get_metrics()
        before = metrics.get_counter("fingerprint.errors")
        runtime = AsyncMock()
        runtime.evaluate = AsyncMock(side_effect=RuntimeError("boom"))
        await dom_fingerprint(runtime)
        after = metrics.get_counter("fingerprint.errors")
        assert after == before + 1
