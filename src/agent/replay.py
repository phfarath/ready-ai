"""Deterministic replay manifests (READY-AI-T-PH3A-REPLAY-MANIFEST).

Author-once: a declarative flow that already passed verification compiles
into a versioned, self-contained replay manifest (actions + asserts +
fingerprints + idempotency keys). Replaying a manifest re-executes the
exact declared behavior through the same deterministic executor core —
with zero LLM involvement (3B adds the drift gate on top).

Sensitivity note: a manifest inherits exactly what the source flow
declared (including typed text needed to replay). It carries no cookies
or credentials — compile refuses flows with credential auto-login, since
replay cannot perform it without an LLM. Treat a manifest like the flow
file it was compiled from: executable, not a log.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..api.models import (
    FlowAction,
    FlowAssertion,
    FlowExtraction,
    FlowSpec,
    FlowStepSpec,
)

logger = logging.getLogger(__name__)

REPLAY_MANIFEST_VERSION = 1
REPLAY_MANIFEST_KIND = "ready-ai-replay-manifest"


def _action_payload(action: FlowAction) -> dict[str, Any]:
    """Full declared action params (declared fields + extras, unmasked).

    Unlike run reports (which mask typed text), the manifest is an
    executable artifact: replay needs the real values. Sensitivity is
    inherited from the source flow file, never expanded — compile adds
    no secrets of its own (credentials are refused outright).
    """
    payload = {
        k: v
        for k, v in {
            **action.model_dump(exclude_none=True),
            **(action.model_extra or {}),
        }.items()
        if v is not None
    }
    payload.pop("retries", None)
    return payload


def compile_manifest(flow: FlowSpec, result: Mapping[str, Any]) -> dict[str, Any]:
    """Compile a verified flow run into a replay manifest.

    Fail-closed: only a ``passed`` result compiles; flows with credential
    auto-login are refused (replay is zero-LLM and cannot log in); every
    step must carry the ``fingerprint_pre`` captured during the authoring
    run, otherwise the manifest would be unverifiable by the 3B drift gate.
    """
    status = result.get("status")
    if status != "passed":
        raise ValueError(
            f"refusing to compile a {status!r} run into a replay manifest; "
            "only verified (passed) flows are replayable"
        )
    if getattr(flow, "username", None) or getattr(flow, "password", None):
        raise ValueError(
            "refusing to compile a flow with credential auto-login: replay "
            "is zero-LLM and cannot fill a login form — use a persistent "
            "profile or cookies instead"
        )
    result_steps = list(result.get("steps") or [])
    if len(result_steps) != len(flow.steps):
        raise ValueError(
            f"result has {len(result_steps)} step(s) for a {len(flow.steps)}-step "
            "flow; refusing to compile a partial run"
        )
    manifest_steps: list[dict[str, Any]] = []
    for position, (step, report) in enumerate(
        zip(flow.steps, result_steps), start=1
    ):
        if not isinstance(report, dict) or report.get("status") != "passed":
            raise ValueError(
                f"refusing to compile: step {position} did not pass "
                f"({report.get('status') if isinstance(report, dict) else report!r})"
            )
        fingerprint = report.get("fingerprint_pre") or ""
        if not fingerprint:
            raise ValueError(
                f"refusing to compile: step {position} carries no "
                "fingerprint_pre from the authoring run"
            )
        manifest_steps.append(
            {
                "index": position,
                "name": step.name,
                "actions": [_action_payload(a) for a in step.actions],
                "asserts": [
                    a.model_dump(exclude_none=True) for a in step.asserts
                ],
                "extract": [
                    e.model_dump(exclude_none=True) for e in step.extract
                ],
                "retries": step.retries,
                "policy": step.policy,
                "confirm": step.confirm,
                "irreversible": step.irreversible,
                "idempotency_key": report.get("idempotency_key")
                or step.idempotency_key
                or "",
                "fingerprint_pre": fingerprint,
            }
        )
    return {
        "version": REPLAY_MANIFEST_VERSION,
        "kind": REPLAY_MANIFEST_KIND,
        "flow": flow.name,
        "url": flow.url,
        "source_run_id": result.get("run_id") or flow.run_id,
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "effect_policy": flow.effect_policy,
        "retries": flow.retries,
        "steps": manifest_steps,
    }


def manifest_to_flow_spec(manifest: Mapping[str, Any]) -> FlowSpec:
    """Rebuild an executable FlowSpec from a manifest (replay path).

    Manifest-only keys (index, fingerprint_pre) are dropped — behavior is
    exactly what the authoring flow declared. Version/kind/shape problems
    fail closed; the 3B drift gate compares fingerprints separately.
    """
    if not isinstance(manifest, Mapping):
        raise TypeError("replay manifest must be a JSON object")
    if manifest.get("kind") != REPLAY_MANIFEST_KIND:
        raise ValueError(
            f"not a replay manifest (kind={manifest.get('kind')!r})"
        )
    version = manifest.get("version")
    if version != REPLAY_MANIFEST_VERSION:
        raise ValueError(
            f"unsupported replay manifest version {version!r}; "
            f"this engine replays v{REPLAY_MANIFEST_VERSION}"
        )
    url = manifest.get("url")
    raw_steps = manifest.get("steps")
    if not url or not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("replay manifest has no url or no steps")
    steps: list[FlowStepSpec] = []
    for position, raw in enumerate(raw_steps, start=1):
        if not isinstance(raw, dict):
            raise TypeError(f"replay manifest step {position} is not an object")
        try:
            steps.append(
                FlowStepSpec(
                    name=raw.get("name"),
                    actions=[
                        FlowAction(**a) for a in (raw.get("actions") or [])
                    ],
                    asserts=[
                        FlowAssertion(**a) for a in (raw.get("asserts") or [])
                    ],
                    extract=[
                        FlowExtraction(**e) for e in (raw.get("extract") or [])
                    ],
                    retries=raw.get("retries"),
                    policy=raw.get("policy"),
                    confirm=bool(raw.get("confirm", False)),
                    irreversible=bool(raw.get("irreversible", False)),
                    idempotency_key=raw.get("idempotency_key") or None,
                )
            )
        except Exception as exc:
            raise ValueError(
                f"replay manifest step {position} is invalid: {exc}"
            ) from exc
    return FlowSpec(
        name=manifest.get("flow"),
        url=url,
        steps=steps,
        retries=int(manifest.get("retries", 1)),
        effect_policy=manifest.get("effect_policy") or "write",
    )


def write_manifest(
    manifest: Mapping[str, Any], output_dir: str | Path, run_id: str
) -> Path:
    """Persist a compiled manifest next to the run result."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{run_id}_replay_manifest.json"
    path.write_text(json.dumps(dict(manifest), indent=2), encoding="utf-8")
    logger.info("═══ Replay manifest saved to: %s", path)
    return path


def read_manifest(path: str | Path) -> dict[str, Any]:
    """Load and shape-validate a manifest file (version enforced)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    # Reuse the strict constructor as the validator: unknown versions,
    # wrong kinds, and malformed steps all fail closed here.
    manifest_to_flow_spec(raw)
    return raw


__all__ = [
    "REPLAY_MANIFEST_KIND",
    "REPLAY_MANIFEST_VERSION",
    "compile_manifest",
    "manifest_to_flow_spec",
    "read_manifest",
    "write_manifest",
]
