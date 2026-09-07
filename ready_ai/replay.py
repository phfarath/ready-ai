"""SDK-side replay interpretation (Fase 3 — PH3B drift gate + PH3C custo).

O motor (compilar/executar) mora no engine (``src.agent.replay`` +
``AgenticLoop.run_flow(allow_llm=False)``); este módulo interpreta o
resultado no lado do SDK, sem nenhum import do engine no load:

- drift gate: compara ``fingerprint_pre`` por step do manifesto contra o
  resultado do replay; divergência nunca cura em silêncio — gera
  ``DRIFT_SUSPECTED`` observável e o chamador decide o fallback
  agêntico (heal path mantém LLM).
- gatilho de fallback: drift OU passo com ``status != passed``
  (inclui ``failed``/``pending_confirmation``/``skipped``).
- custo: antes/depois no formato do ``run_summary`` do observability
  (``llm_calls`` / ``llm_tokens`` / ``llm_cost_usd``); replay puro é
  zero, heal contabilizado à parte.

Funções puras (stdlib apenas): testáveis sem browser, LLM ou deps.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

DRIFT_SUSPECTED = "DRIFT_SUSPECTED"
"""Sinal observável de drift — um canal sozinho nunca auto-cura (gate Fase 4)."""


def _as_steps(container: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Aceita um manifesto/resultado (``{"steps": [...]}``) ou a lista direta."""
    if isinstance(container, Mapping):
        steps = container.get("steps") or []
        return [s for s in steps if isinstance(s, Mapping)]
    return [s for s in container if isinstance(s, Mapping)]


def compare_fingerprints(
    manifest: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    result: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Compara ``fingerprint_pre`` por step; fail-closed em ausência/divergência.

    Retorna uma entrada por step divergente ou sem fingerprint em qualquer
    lado: ``{index, expected, actual, reason}``. Lista vazia = sem drift.
    """
    expected_steps = _as_steps(manifest)
    actual_steps = _as_steps(result)
    drifts: list[dict[str, Any]] = []
    for position, (expected, actual) in enumerate(
        zip(expected_steps, actual_steps), start=1
    ):
        want = expected.get("fingerprint_pre") or ""
        got = actual.get("fingerprint_pre") or ""
        index = actual.get("index", position)
        if not want or not got:
            drifts.append(
                {
                    "index": index,
                    "expected": want,
                    "actual": got,
                    "reason": "missing-fingerprint",
                }
            )
        elif want != got:
            drifts.append(
                {
                    "index": index,
                    "expected": want,
                    "actual": got,
                    "reason": "fingerprint-diverged",
                }
            )
    if len(actual_steps) != len(expected_steps):
        drifts.append(
            {
                "index": -1,
                "expected": f"{len(expected_steps)}-steps",
                "actual": f"{len(actual_steps)}-steps",
                "reason": "step-count-mismatch",
            }
        )
    return drifts


def failed_steps(
    result: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Steps cujo ``status`` não é ``passed`` (falha, pausa, skip)."""
    out: list[dict[str, Any]] = []
    for position, step in enumerate(_as_steps(result), start=1):
        if step.get("status") != "passed":
            out.append(
                {
                    "index": step.get("index", position),
                    "name": step.get("name"),
                    "status": step.get("status"),
                    "failure_reason": step.get("failure_reason") or "",
                }
            )
    return out


def detect_drift(
    manifest: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    result: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Gate único do SDK: drift OU passo não-passed pede fallback agêntico.

    Retorna ``{drift_suspected, signal, drifts, failed_steps}`` onde
    ``signal`` é ``DRIFT_SUSPECTED`` quando há algo a curar, senão ``""``.
    Nunca cura — só sinaliza para o chamador re-executar com LLM.
    """
    drifts = compare_fingerprints(manifest, result)
    failed = failed_steps(result)
    suspected = bool(drifts or failed)
    return {
        "drift_suspected": suspected,
        "signal": DRIFT_SUSPECTED if suspected else "",
        "drifts": drifts,
        "failed_steps": failed,
    }


def _cost_of(summary: Mapping[str, Any] | None) -> dict[str, float]:
    """Normaliza o shape do ``run_summary`` (tolerante a ausências)."""
    summary = summary or {}
    tokens = summary.get("llm_tokens") or {}
    try:
        calls = int(summary.get("llm_calls") or 0)
    except (TypeError, ValueError):
        calls = 0
    try:
        cost = float(summary.get("llm_cost_usd") or 0.0)
    except (TypeError, ValueError):
        cost = 0.0
    return {
        "llm_calls": calls,
        "llm_prompt_tokens": int(tokens.get("prompt") or 0),
        "llm_completion_tokens": int(tokens.get("completion") or 0),
        "llm_cost_usd": cost,
    }


def summarize_replay_cost(
    author_summary: Mapping[str, Any] | None,
    replay_summary: Mapping[str, Any] | None,
    *,
    heal_cost_usd: float = 0.0,
) -> dict[str, Any]:
    """Custo antes (autoria com LLM) vs depois (replay zero-token).

    ``heal_cost_usd`` contabiliza o fallback agêntico à parte, quando
    houver — replay puro publica zero. Retorna blocos ``before``/``after``/
    ``saved`` (nunca negativo: heal pode superar a autoria).
    """
    before = _cost_of(author_summary)
    after = _cost_of(replay_summary)
    try:
        heal = max(0.0, float(heal_cost_usd or 0.0))
    except (TypeError, ValueError):
        heal = 0.0
    after_total = after["llm_cost_usd"] + heal
    saved = before["llm_cost_usd"] - after_total
    return {
        "before": before,
        "after": {**after, "heal_cost_usd": heal, "total_cost_usd": after_total},
        "saved_usd": max(0.0, saved),
        "zero_token": after["llm_calls"] == 0 and heal == 0.0,
    }


__all__ = [
    "DRIFT_SUSPECTED",
    "compare_fingerprints",
    "detect_drift",
    "failed_steps",
    "summarize_replay_cost",
]
