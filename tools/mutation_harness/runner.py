"""CDP driver: applies declared mutations to a live app and measures
the detection channels (visual diff, DOM fingerprint) per mutation.

Flow per drill:

1. Launch a BrowserSession, navigate to ``config.app_url``.
2. Capture baseline: screenshot + ``dom_fingerprint``.
3. For each declared mutation: apply via ``Runtime.evaluate``, settle,
   measure both channels, then reload the page so no mutation leaks
   into the next measurement (declarative isolation — no revert code).
4. Compute the scorecard and write ``healer_scorecard.json`` +
   ``healer_scorecard.html`` (rendered via src.docs.report_html).
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

    session = BrowserSession(port=port or _free_port(), headless=headless)
    outcomes: list[MutationOutcome] = []

    try:
        await session.setup()
        page, runtime = session.page, session.runtime

        logger.info("Mutation drill: %d mutations against %s",
                    len(config.mutations), app_url)
        await page.navigate(app_url)
        baseline_png = shots_dir / "_baseline.png"
        _write_b64_png(await page.screenshot(), baseline_png)
        baseline_fp = await dom_fingerprint(runtime)

        for mutation in config.mutations:
            outcome = MutationOutcome(
                mutation_id=mutation.id, kind=mutation.kind, applied=False
            )
            outcomes.append(outcome)
            try:
                applied = await runtime.evaluate(mutation.apply_js())
            except Exception as exc:
                outcome.error = f"evaluate failed: {exc}"
                logger.warning("Mutation %s failed to evaluate: %s",
                               mutation.id, exc)
            else:
                if applied:
                    outcome.applied = True
                    await asyncio.sleep(config.settle_ms / 1000)

                    shot_path = shots_dir / f"m_{mutation.id}.png"
                    _write_b64_png(await page.screenshot(), shot_path)
                    diff_path = diffs_dir / f"m_{mutation.id}.png"
                    diff = compare_screenshots(
                        baseline_png, shot_path, diff_path,
                        threshold=config.threshold,
                    )
                    outcome.visual_similarity = round(diff.similarity_score, 4)
                    outcome.visual_flagged = diff.is_outdated

                    fp = await dom_fingerprint(runtime)
                    outcome.dom_flagged = (
                        fp.startswith(FP_ERROR_PREFIX) or fp != baseline_fp
                    )
                else:
                    outcome.error = "mutation JS returned falsy (stale selector?)"
                    logger.warning("Mutation %s did not apply", mutation.id)

            # Reset app state: full reload isolates each measurement.
            await page.navigate(app_url)

    finally:
        await session.teardown()

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
    return scorecard
