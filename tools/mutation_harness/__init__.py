"""Mutation-injection harness for doc-suite healer evaluation.

Injects declared mutations into a test/staging app via CDP, measures how the
drift-detection channels (visual diff, DOM fingerprint) react, and computes a
healer scorecard: detection rate and false-positive rate per regime
(single-channel vs multi-causal gated).

Public API:
    load_config(path)          -> DrillConfig
    run_mutation_drill(...)    -> scorecard dict (async, drives real Chrome)
    compute_scorecard(...)     -> scorecard dict (pure, unit-testable)
"""

from .mutations import DrillConfig, Mutation, load_config
from .scorecard import REGIMES, MutationOutcome, compute_scorecard
from .runner import run_mutation_drill

__all__ = [
    "DrillConfig",
    "Mutation",
    "MutationOutcome",
    "REGIMES",
    "compute_scorecard",
    "load_config",
    "run_mutation_drill",
]
