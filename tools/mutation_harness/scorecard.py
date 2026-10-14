"""Healer scorecard: TP/FP metrics per detection regime.

The measured thing is the *channel stack* that drives the auto-healer:

- ``single_visual`` — flag iff visual similarity dropped below threshold.
- ``single_dom`` — flag iff the DOM fingerprint changed.
- ``gated`` — flag iff BOTH channels flag (the US5 multi-causal gate:
  a single channel alone yields DRIFT_SUSPECTED, never silent heal).

Ground truth comes from the mutation ``kind``: ``semantic`` mutations
should be detected (flagged = TP, missed = FN); ``noise`` mutations
should not (flagged = FP, clean = TN). Mutations that failed to apply
are excluded from the confusion matrix — a stale declaration is a
harness failure, not a detection miss.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

REGIMES = ("single_visual", "single_dom", "gated")

_VERDICTS = ("TP", "FP", "FN", "TN")


@dataclass
class MutationOutcome:
    """Measured channel reaction to one applied mutation."""

    mutation_id: str
    kind: str  # "semantic" | "noise"
    applied: bool
    error: Optional[str] = None
    visual_similarity: Optional[float] = None
    visual_flagged: bool = False
    dom_flagged: bool = False


def regime_flagged(regime: str, outcome: MutationOutcome) -> bool:
    """Whether `regime` raises a confirmed drift flag for `outcome`."""
    if regime == "single_visual":
        return outcome.visual_flagged
    if regime == "single_dom":
        return outcome.dom_flagged
    if regime == "gated":
        return outcome.visual_flagged and outcome.dom_flagged
    raise ValueError(f"Unknown regime: {regime}")


def _verdict(flagged: bool, kind: str) -> str:
    positive = kind == "semantic"
    if flagged:
        return "TP" if positive else "FP"
    return "FN" if positive else "TN"


def _rate(num: int, den: int) -> Optional[float]:
    return num / den if den else None


def compute_scorecard(
    outcomes: list[MutationOutcome],
    *,
    app_url: str,
    threshold: float,
) -> dict:
    """Compute the healer scorecard from measured per-mutation outcomes.

    Returns a JSON-serializable dict with per-mutation verdicts and a
    per-regime confusion matrix + rates. ``detection_rate`` is the
    mutation score (fraction of semantic mutations detected); ``fpr``
    is the fraction of noise mutations wrongly flagged.
    """
    mutations = []
    applied = [o for o in outcomes if o.applied]

    for o in outcomes:
        mutations.append(
            {
                "id": o.mutation_id,
                "kind": o.kind,
                "applied": o.applied,
                "error": o.error,
                "visual_similarity": o.visual_similarity,
                "visual_flagged": o.visual_flagged,
                "dom_flagged": o.dom_flagged,
                "verdicts": (
                    {r: _verdict(regime_flagged(r, o), o.kind) for r in REGIMES}
                    if o.applied
                    else {}
                ),
            }
        )

    regimes = {}
    for regime in REGIMES:
        counts = {v.lower(): 0 for v in _VERDICTS}
        for o in applied:
            counts[_verdict(regime_flagged(regime, o), o.kind).lower()] += 1
        detection_rate = _rate(counts["tp"], counts["tp"] + counts["fn"])
        fpr = _rate(counts["fp"], counts["fp"] + counts["tn"])
        regimes[regime] = {
            **counts,
            "detection_rate": detection_rate,
            "fpr": fpr,
            "mutation_score": detection_rate,
        }

    skipped = len(outcomes) - len(applied)
    return {
        "app_url": app_url,
        "threshold": threshold,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_mutations": len(outcomes),
        "applied_mutations": len(applied),
        "skipped_mutations": skipped,
        "regimes": regimes,
        "mutations": mutations,
    }
