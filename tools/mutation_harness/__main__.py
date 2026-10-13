"""CLI: ``python -m tools.mutation_harness --config drill.yaml --out out/``"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from .runner import run_mutation_drill


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.mutation_harness",
        description="Inject declared mutations via CDP and score the healer.",
    )
    parser.add_argument("--config", required=True, help="mutations YAML")
    parser.add_argument("--url", default=None, help="override config app_url")
    parser.add_argument("--out", default="mutation_drill_out", help="output dir")
    parser.add_argument("--port", type=int, default=None, help="CDP port")
    parser.add_argument(
        "--headed", action="store_true", help="run Chrome with UI (debug)"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    scorecard = asyncio.run(
        run_mutation_drill(
            args.config,
            url=args.url,
            output_dir=args.out,
            port=args.port,
            headless=not args.headed,
        )
    )
    gated = scorecard["regimes"]["gated"]
    det = gated["detection_rate"]
    fpr = gated["fpr"]
    print(
        f"gated detection_rate={det if det is not None else 'n/a'} "
        f"fpr={fpr if fpr is not None else 'n/a'} "
        f"({scorecard['applied_mutations']}/{scorecard['total_mutations']} applied)"
    )
    print(json.dumps(scorecard["regimes"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
