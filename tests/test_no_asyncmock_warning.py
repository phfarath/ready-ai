"""Regression tests for VAL-QUAL-001: AsyncMock warning in the test_runner path.

Root cause: the e2e DocTestRunner mock suite (``_setup_mocks`` in
``test_e2e_doc_test.py``) created ``llm = AsyncMock()`` without configuring
``complete_with_vision_multi``.  During a DRIFT step ``describe_visual_change``
does::

    description = await llm.complete_with_vision_multi(...)   # -> AsyncMock
    return description.strip()                                # -> coroutine!

``description`` is an ``AsyncMock`` (the default return value of awaiting an
unconfigured ``AsyncMock``), so ``description.strip()`` is a *child* AsyncMock
whose call yields a **coroutine** that is returned but never awaited.  When that
coroutine is garbage-collected Python emits::

    RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited

These tests assert the mock suite returns real ``str`` values so no un-awaited
coroutine is created, allowing ``pytest -W error::RuntimeWarning`` to run clean.
"""

import pytest


def _e2e_mocks():
    """Import and call the shared e2e mock helper lazily (keeps PIL import side
    effects isolated to tests that actually need them)."""
    from tests.test_e2e_doc_test import _setup_mocks

    return _setup_mocks()


@pytest.mark.asyncio
async def test_setup_mocks_llm_vision_returns_str():
    """The e2e mock llm must return a *str* from ``complete_with_vision_multi``
    so ``describe_visual_change`` does not create an un-awaited coroutine."""
    chrome_proc, conn, page, input_domain, runtime, llm = _e2e_mocks()

    result = await llm.complete_with_vision_multi(
        prompt="Compare", images_b64=["a", "b"], role="semantic_diff"
    )

    assert isinstance(result, str), (
        f"complete_with_vision_multi must return str, got {type(result).__name__}"
    )


@pytest.mark.asyncio
async def test_describe_visual_change_returns_str_with_e2e_mock():
    """End-to-end check: running ``describe_visual_change`` with the e2e mock
    llm must yield a ``str`` (not a stray coroutine object)."""
    from pathlib import Path

    from src.docs.semantic_diff import describe_visual_change

    fixture = Path(__file__).parent / "fixtures" / "sample_doc" / "screenshots" / "step_01.png"

    chrome_proc, conn, page, input_domain, runtime, llm = _e2e_mocks()

    raw = await describe_visual_change(
        baseline_path=str(fixture),
        current_path=str(fixture),
        step_title="Login",
        llm=llm,
    )

    assert isinstance(raw, str), (
        f"describe_visual_change must return str, got {type(raw).__name__}"
    )
