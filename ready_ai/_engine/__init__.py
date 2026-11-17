"""Internal engine of ready-ai (``ready_ai._engine``).

This package is the implementation detail of the engine. External
consumers must use the public SDK façade instead::

    from ready_ai import ReadyAI, Flow, FlowStep, RunResult, BrowserOptions

Do not build against ``ready_ai._engine.*`` imports: they are not a stable contract.
"""
