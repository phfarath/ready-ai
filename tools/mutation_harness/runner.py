"""CDP driver: applies declared mutations to a live app and measures
the detection channels (visual diff, DOM fingerprint) per mutation.

Flow per drill:

1. Launch a BrowserSession, navigate to ``config.app_url``.
2. Capture baseline: screenshot + ``dom_fingerprint``.
3. For each declared mutation: apply via ``Runtime.evaluate``, settle,
   measure both channels, then reload the page AND recapture the
   baseline — so non-deterministic page churn across reloads is never
   attributed to the mutation.
4. Per-mutation measurement failures are recorded on the outcome
   (``error``) and the drill continues; failures that leave the page
   state untrusted (reload, baseline capture) abort the drill — but the
   scorecard is still written from whatever was measured.

Apply contract: ``Runtime.evaluate`` exceptions (``exceptionDetails``)
mean "not applied" — the shared ``RuntimeDomain.evaluate`` swallows
them into a truthy error-description string, so the apply call goes one
level lower and inspects the raw response.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import socket
from pathlib import Path
from typing import Optional

from src.agent.browser_session import BrowserSession
from src.agent.dom_utils import FP_ERROR_PREFIX, dom_fingerprint
from src.docs.report_html import render_healer_scorecard
from src.docs.visual_diff import compare_screenshots

from .mutations import DrillConfig, load_config
from .scorecard import MutationOutcome, compute_scorecard

logger = logging.getLogger(__name__)

SCORECARD_JSON = "healer_scorecard.json"
SCORECARD_HTML = "healer_scorecard.html"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _write_b64_png(b64_data: str, path: Path) -> None:
    path.write_bytes(base64.b64decode(b64_data))


async def _evaluate_apply(conn, expression: str) -> tuple[bool, Optional[str]]:
    """Apply mutation JS via raw ``Runtime.evaluate``.

    Returns ``(applied, error)``. ``exceptionDetails`` in the response
    means the mutation JS threw → not applied (a malformed selector or
    throwing ``js`` op must surface as a stale declaration, never as a
    phantom detector miss).
    """
    result = await conn.send(
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        },
    )
    details = result.get("exceptionDetails")
    if details:
        exception = details.get("exception") or {}
        desc = exception.get("description") or details.get("text") or "unknown"
        return False, f"mutation JS threw: {desc}"
    remote = result.get("result", {})
    return bool(remote.get("value")), None


async def _capture_baseline(page, runtime, shot_path: Path) -> str:
    """Screenshot + DOM fingerprint of the clean page.

    A fingerprint that comes back as the ``__fp_error__`` sentinel means
    the CDP evaluation failed — comparing anything against it flags
    every mutation, so it aborts loudly instead of poisoning the
    scorecard.
    """
    _write_b64_png(await page.screenshot(), shot_path)
    fp = await dom_fingerprint(runtime)
    if fp.startswith(FP_ERROR_PREFIX):
        raise RuntimeError(
            "baseline DOM fingerprint failed (CDP evaluation error)"
        )
    return fp


async def run_mutation_drill(
    config_path: str | Path,
    *,
    url: Optional[str] = None,
    output_dir: str | Path = "mutation_drill_out",
    port: Optional[int] = None,
    headless: bool = True,
) -> dict:
    """Run a mutation drill against a live app and emit the scorecard.

    Args:
        config_path: YAML file declaring mutations (see mutations.py).
        url: Override for ``config.app_url``.
        output_dir: Directory receiving shots/, diffs/ and scorecard files.
        port: CDP port (auto-picks a free one when None).
        headless: Chrome headless flag.

    Returns:
        The scorecard dict (also written to ``healer_scorecard.json``).
    """
    config: DrillConfig = load_config(config_path)
    app_url = url or config.app_url
    if not app_url:
        raise ValueError("No app_url: set it in the YAML config or pass --url")

    out = Path(output_dir)
    shots_dir = out / "shots"
    diffs_dir = out / "diffs"
    shots_dir.mkdir(parents=True, exist_ok=True)
    diffs_dir.mkdir(parents=True, exist_ok=True)
    baseline_png = shots_dir / "_baseline.png"

    session = BrowserSession(port=port or _free_port(), headless=headless)
    outcomes: list[MutationOutcome] = []
    abort_error: Optional[BaseException] = None

    try:
        await session.setup()
        conn, page, runtime = session.conn, session.page, session.runtime

        logger.info("Mutation drill: %d mutations against %s",
                    len(config.mutations), app_url)
        await page.navigate(app_url)
        baseline_fp = await _capture_baseline(page, runtime, baseline_png)

        for mutation in config.mutations:
            outcome = MutationOutcome(
                mutation_id=mutation.id, kind=mutation.kind, applied=False
            )
            outcomes.append(outcome)
            try:
                applied, apply_error = await _evaluate_apply(
                    conn, mutation.apply_js()
                )
                if apply_error:
                    outcome.error = apply_error
                    logger.warning("Mutation %s: %s", mutation.id, apply_error)
                elif applied:
                    outcome.applied = True
                    await asyncio.sleep(config.settle_ms / 1000)

                    shot_path = shots_dir / f"m_{mutation.id}.png"
                    _write_b64_png(await page.screenshot(), shot_path)
                    diff = compare_screenshots(
                        baseline_png, shot_path,
                        diffs_dir / f"m_{mutation.id}.png",
                        threshold=config.threshold,
                    )
                    outcome.visual_similarity = round(diff.similarity_score, 4)
                    outcome.visual_flagged = diff.is_outdated

                    fp = await dom_fingerprint(runtime)
                    outcome.dom_flagged = (
                        fp.startswith(FP_ERROR_PREFIX) or fp != baseline_fp
                    )
                else:
                    outcome.error = (
                        "mutation JS returned falsy (stale selector?)"
                    )
                    logger.warning("Mutation %s did not apply", mutation.id)
            except Exception as exc:
                # Measurement failure: record on the outcome and move on —
                # one broken mutation must not discard the whole drill.
                outcome.error = f"measurement failed: {type(exc).__name__}: {exc}"
                logger.warning("Mutation %s: %s", mutation.id, outcome.error)

            # Reset app state: full reload isolates each measurement, and
            # the baseline is recaptured so non-deterministic page churn is
            # never attributed to the next mutation. A failed reload or
            # baseline leaves the page state untrusted → abort.
            await page.navigate(app_url)
            baseline_fp = await _capture_baseline(page, runtime, baseline_png)

    except Exception as exc:
        abort_error = exc
        logger.error("Mutation drill aborted: %s", exc)
    finally:
        await session.teardown()

    if not outcomes:
        raise abort_error or RuntimeError("drill produced no outcomes")

    scorecard = compute_scorecard(
        outcomes, app_url=app_url, threshold=config.threshold
    )
    (out / SCORECARD_JSON).write_text(
        json.dumps(scorecard, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    render_healer_scorecard(scorecard, out / SCORECARD_HTML)
    logger.info(
        "Mutation drill done: %d/%d applied — scorecard in %s",
        scorecard["applied_mutations"], scorecard["total_mutations"], out,
    )
    if abort_error is not None:
        raise abort_error
    return scorecard
