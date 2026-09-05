"""ready_ai — public Python SDK for the ready-ai automation engine.

This package is the stable, importable façade for the engine, which
internally ships as ``src``. Consume ONLY this package::

    from ready_ai import ReadyAI, Flow, FlowStep, RunResult, BrowserOptions

Public surface
--------------
- ``BrowserOptions`` — browser execution context (``headless``, CDP
  ``port``, and a ``profile`` *reference*). Profiles are allowlisted
  names/paths resolved by the façade; secrets are never serializable.
- ``Flow`` / ``FlowStep`` — declarative run documents (actions,
  expectations, extractions) that validate URL, timeouts, effect policy
  and profile references, and carry a ``version`` schema for forward
  compatibility (unknown keys ignored, newer versions tolerated).
- ``RunResult`` — structured, sanitized run output with ``run_id``,
  ``status``, ``steps`` and only the allowed ``artifacts`` written
  inside the output directory.
- ``ReadyAI`` — the entry point; runs flows on the engine behind a
  profile allowlist registry. No ``src.*`` imports are ever needed on
  the consumer side.

Minimal example::

    import asyncio
    from ready_ai import ReadyAI, Flow, FlowStep, BrowserOptions

    flow = Flow(
        name="checkout-smoke",
        url="https://app.example.com/start",
        steps=[FlowStep(name="Open cart", actions=[])],
    )

    async def main():
        ai = ReadyAI(profiles={"qa": "/secure/qa-cookies.json"})
        result = await ai.run_flow(flow, browser=BrowserOptions(profile="qa"))
        print(result.status, result.run_id)

    asyncio.run(main())
"""

from __future__ import annotations

from .client import ReadyAI, ReadyAIError, RunTimeoutError, UnknownProfileError
from .models import (
    SCHEMA_VERSION,
    BrowserOptions,
    EffectPolicy,
    Flow,
    FlowAction,
    FlowAssertion,
    FlowExtraction,
    FlowStep,
    Profile,
    RunResult,
    RunStep,
)

__version__ = "0.2.0"

__all__ = [
    "SCHEMA_VERSION",
    "BrowserOptions",
    "EffectPolicy",
    "Flow",
    "FlowAction",
    "FlowAssertion",
    "FlowExtraction",
    "FlowStep",
    "Profile",
    "ReadyAI",
    "ReadyAIError",
    "RunResult",
    "RunStep",
    "RunTimeoutError",
    "UnknownProfileError",
    "__version__",
]
