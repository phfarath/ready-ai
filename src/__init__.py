"""Internal ``src`` package of ready-ai.

This package is the implementation detail of the engine. External
consumers must use the public SDK façade instead::

    from ready_ai import ReadyAI, Flow, FlowStep, RunResult, BrowserOptions

Do not build against ``src.*`` imports: they are not a stable contract.
Existing internal imports (``from src.agent.loop import AgenticLoop``,
``from src.api import server``, ...) keep working during the transition.
"""
