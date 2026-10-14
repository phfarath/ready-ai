"""Typed usage example for the public ``ready_ai`` SDK.

This file is part of the public typing contract: CI runs ``mypy`` over
``ready_ai/`` and ``examples/`` on every supported Python version
(3.10–3.12). It exercises the documented surface — declarative ``Flow``
construction, ``run``/``run_sync``, event streaming via ``RunHandle``,
cooperative cancellation, checkpoint ``resume`` and the exception
taxonomy — without needing Chrome or network (execution is left to the
caller; constructing and driving the API must type-check standalone).
"""

from __future__ import annotations

import asyncio
from typing import List

import ready_ai
from ready_ai import (
    BrowserOptions,
    Flow,
    FlowAction,
    FlowAssertion,
    FlowStep,
    ReadyAI,
    ReadyAIError,
    RunCancelledError,
    RunEvent,
    RunResult,
    RunTimeoutError,
)


def build_flow() -> Flow:
    """Declarative run document — validated at construction."""
    return Flow(
        name="checkout-smoke",
        url="https://app.example.com/start",
        timeout_s=60.0,
        steps=[
            FlowStep(
                name="Open cart",
                actions=[
                    FlowAction(action="click", selector="#cart"),
                    FlowAction(action="wait", text="0.5"),
                ],
                asserts=[
                    FlowAssertion(type="url_contains", expected="/cart"),
                ],
            ),
        ],
    )


async def run_and_watch(ai: ReadyAI, flow: Flow) -> RunResult:
    """Stream ordered events while the run proceeds in the background."""
    handle = ai.start(flow, browser=BrowserOptions(headless=True))
    events: List[RunEvent] = []
    async for event in handle.events():
        events.append(event)
    assert events[-1].type == "run_finished"
    return await handle.result()


async def cancel_after_first_step(ai: ReadyAI, flow: Flow) -> RunResult:
    """Cooperative cancellation: the in-flight step finishes, the rest
    are reported skipped and the run resolves ``cancelled``."""
    handle = ai.start(flow)
    async for event in handle.events():
        if event.type == "step_finished":
            await handle.cancel()
    return await handle.result()


async def resume_checkpoint(ai: ReadyAI, run_id: str) -> RunResult:
    """Resume a run paused at a human checkpoint from its run_id token."""
    return await ai.resume(build_flow(), run_id=run_id)


async def main() -> None:
    ai = ReadyAI(output_dir="./output", model="gpt-4o-mini")
    flow = build_flow()

    ai.validate_config(flow)  # pre-flight: profiles, effect ceilings
    try:
        result = await run_and_watch(ai, flow)
    except RunCancelledError:
        result = await resume_checkpoint(ai, run_id="previous-run")
    except RunTimeoutError:
        raise
    except ReadyAIError as exc:
        raise SystemExit(f"run failed: {exc}") from exc

    print(ready_ai.__version__, result.status, result.run_id)


def sync_entrypoint() -> RunResult:
    """``run_sync`` is the only API safe to call outside an event loop."""
    ai = ReadyAI(output_dir="./output")
    return ai.run_sync(build_flow())


if __name__ == "__main__":
    asyncio.run(main())
