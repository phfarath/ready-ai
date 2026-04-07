#!/usr/bin/env python3
"""
ready-ai: Agentic browser automation for SaaS documentation.

Uses raw CDP (Chrome DevTools Protocol) + LLM to navigate SaaS UIs,
capture screenshots, and generate annotated Markdown documentation.

Usage:
    ready-ai run --goal "Documentar fluxo de login" --url "https://app.example.com"
    ready-ai api --port 8000
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
import yaml

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    import tomli as tomllib

load_dotenv()  # Load .env file (API keys etc.)

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

RUN_DEFAULTS = {
    "goal": None,
    "url": None,
    "title": None,
    "language": None,
    "model": "gpt-4o-mini",
    "output": "./output",
    "port": 9222,
    "headless": False,
    "max_critic_rounds": 2,
    "annotation_model": None,
    "cookies_file": None,
    "username": None,
    "password": None,
    "verbose": False,
    "config": None,
    "run_id": "local_run",
    "resume": False,
    "plan_only": False,
}

CONFIG_KEYS = set(RUN_DEFAULTS) - {"config"}


class RunConfigError(ValueError):
    """Raised when the CLI run config is invalid."""


def setup_logging(verbose: bool = False) -> None:
    """Configure logging with structured output and observability."""
    from src.observability import setup_observability
    setup_observability(verbose=verbose, json_output=not verbose)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="🤖 ready-ai: Agentic browser automation for SaaS documentation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", required=True, help="Sub-commands")

    run_parser = subparsers.add_parser("run", help="Run the documentation agent locally")
    run_parser.add_argument("--goal", "-g", default=argparse.SUPPRESS, help="Documentation goal")
    run_parser.add_argument("--url", "-u", default=argparse.SUPPRESS, help="Target SaaS URL")
    run_parser.add_argument("--title", "-t", default=argparse.SUPPRESS, help="Document title")
    run_parser.add_argument("--language", "-l", default=argparse.SUPPRESS, help="Output language")
    run_parser.add_argument(
        "--model", "-m", default=argparse.SUPPRESS, help="LLM model (default: gpt-4o-mini)"
    )
    run_parser.add_argument("--output", "-o", default=argparse.SUPPRESS, help="Output directory")
    run_parser.add_argument(
        "--port", "-p", type=int, default=argparse.SUPPRESS, help="Chrome debugging port"
    )
    run_parser.add_argument("--headless", action="store_true", default=argparse.SUPPRESS, help="Run headless")
    run_parser.add_argument(
        "--max-critic-rounds",
        type=int,
        default=argparse.SUPPRESS,
        help="Max critic rounds",
    )
    run_parser.add_argument(
        "--annotation-model", default=argparse.SUPPRESS, help="Specific model for vision"
    )
    run_parser.add_argument("--cookies-file", default=argparse.SUPPRESS, help="JSON cookies file")
    run_parser.add_argument("--username", default=argparse.SUPPRESS, help="Username for auto-login")
    run_parser.add_argument("--password", default=argparse.SUPPRESS, help="Password for auto-login")
    run_parser.add_argument("--verbose", "-v", action="store_true", default=argparse.SUPPRESS, help="Verbose debug logging")
    run_parser.add_argument("--config", default=argparse.SUPPRESS, help="YAML or TOML config file")
    run_parser.add_argument("--run-id", default=argparse.SUPPRESS, help="Run identifier for checkpoints")
    run_parser.add_argument("--resume", action="store_true", default=argparse.SUPPRESS, help="Resume from an existing checkpoint")
    run_parser.add_argument("--plan-only", action="store_true", default=argparse.SUPPRESS, help="Generate a plan without executing steps")

    # --- TEST Command ---
    test_parser = subparsers.add_parser("test", help="Test documentation against live UI (self-healing)")
    test_parser.add_argument("--doc", "-d", required=True, help="Path to docs.md file to test")
    test_parser.add_argument("--url", "-u", required=True, help="Target SaaS URL to test against")
    test_parser.add_argument("--model", "-m", default="gpt-4o-mini", help="LLM model (default: gpt-4o-mini)")
    test_parser.add_argument("--threshold", type=float, default=0.85, help="Visual similarity threshold (default: 0.85)")
    test_parser.add_argument("--output", "-o", default="./test-report", help="Test report output directory")
    test_parser.add_argument("--port", "-p", type=int, default=9222, help="Chrome debugging port")
    test_parser.add_argument("--headless", action="store_true", help="Run headless")
    test_parser.add_argument("--cookies-file", default=None, help="JSON cookies file")
    test_parser.add_argument("--username", default=None, help="Username for auto-login")
    test_parser.add_argument("--password", default=None, help="Password for auto-login")
    test_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose debug logging")
    test_parser.add_argument("--watch", action="store_true", help="Re-run tests periodically (use with --watch-interval)")
    test_parser.add_argument("--watch-interval", type=int, default=5, help="Watch interval in minutes (default: 5)")
    test_parser.add_argument("--auto-heal", action="store_true", help="Auto-update docs when drift is detected but steps still pass")
    test_parser.add_argument("--open-pr", action="store_true", help="After auto-heal, commit changes to a new branch and open a PR (implies --auto-heal)")
    test_parser.add_argument("--pr-base-branch", default="dev", help="Base branch for the auto-heal PR (default: dev)")
    test_parser.add_argument("--pr-remote", default="origin", help="Git remote used to push the auto-heal branch (default: origin)")
    test_parser.add_argument("--pr-dry-run", action="store_true", help="Run git steps locally but skip push and PR creation")

    api_parser = subparsers.add_parser("api", help="Start the FastAPI server")
    api_parser.add_argument("--port", "-p", type=int, default=8000, help="API server port")
    api_parser.add_argument("--host", default="0.0.0.0", help="API server host")
    api_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose debug logging")

    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def _load_config_file(config_path: str) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        raise RunConfigError(f"Config file not found: {config_path}")

    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    elif suffix == ".toml":
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    else:
        raise RunConfigError("Config file must end with .toml, .yaml, or .yml")

    if not isinstance(data, dict):
        raise RunConfigError("Config file must contain a flat key/value mapping")

    unknown = sorted(set(data) - CONFIG_KEYS)
    if unknown:
        raise RunConfigError(f"Unknown config keys: {', '.join(unknown)}")

    nested = sorted(key for key, value in data.items() if isinstance(value, (dict, list)))
    if nested:
        raise RunConfigError(f"Nested config values are not supported: {', '.join(nested)}")

    return data


def resolve_run_args(raw_args: argparse.Namespace) -> argparse.Namespace:
    cli_values = vars(raw_args).copy()
    config_path = cli_values.get("config")
    config_values = _load_config_file(config_path) if config_path else {}

    merged = dict(RUN_DEFAULTS)
    merged.update(config_values)
    merged.update({key: value for key, value in cli_values.items() if key != "command"})
    merged["command"] = "run"

    missing = [name for name in ("goal", "url") if not merged.get(name)]
    if missing:
        raise RunConfigError(f"Missing required run options: {', '.join(missing)}")

    merged["resume_from"] = None
    if merged["resume"]:
        checkpoint_path = Path(merged["output"]) / f"{merged['run_id']}_state.json"
        if not checkpoint_path.exists():
            raise RunConfigError(f"Checkpoint not found: {checkpoint_path}")
        merged["resume_from"] = str(checkpoint_path)

    return argparse.Namespace(**merged)


async def async_main_run(args: argparse.Namespace) -> None:
    from src.agent.loop import AgenticLoop

    logger = logging.getLogger("main")
    logger.info("🚀 ready-ai — Local CLI Run")

    loop = AgenticLoop(
        goal=args.goal,
        url=args.url,
        model=args.model,
        annotation_model=args.annotation_model,
        output_dir=args.output,
        port=args.port,
        headless=args.headless,
        max_critic_rounds=args.max_critic_rounds,
        cookies_file=args.cookies_file,
        username=args.username,
        password=args.password,
        title=args.title,
        language=args.language,
        run_id=args.run_id,
        resume_from=args.resume_from,
        plan_only=args.plan_only,
    )

    try:
        result_path = await loop.run()
        if args.plan_only:
            logger.info("✅ Plan saved to checkpoint: %s", result_path)
        else:
            logger.info("✅ Documentation complete! Saved to: %s", result_path)
    except KeyboardInterrupt:
        logger.info("⚠️  Interrupted by user")
        sys.exit(1)
    except Exception as exc:
        logger.error("❌ Failed: %s", exc, exc_info=True)
        sys.exit(1)


async def async_main_test(args: argparse.Namespace) -> None:
    from src.agent.test_runner import DocTestRunner

    logger = logging.getLogger("main")
    logger.info("🧪 ready-ai — Documentation Test Runner")

    # --open-pr implies --auto-heal (publishing nothing makes no sense).
    open_pr = getattr(args, "open_pr", False)
    auto_heal = getattr(args, "auto_heal", False) or open_pr

    runner = DocTestRunner(
        doc_path=args.doc,
        url=args.url,
        model=args.model,
        threshold=args.threshold,
        output_dir=args.output,
        port=args.port,
        headless=args.headless,
        cookies_file=args.cookies_file,
        username=args.username,
        password=args.password,
        auto_heal=auto_heal,
    )

    try:
        if getattr(args, "watch", False):
            await _watch_loop(runner, args.watch_interval, logger, args)
        else:
            report = await runner.run()
            if open_pr:
                _maybe_publish_healing(report, args, logger)
            _handle_report_exit(report, logger)
    except KeyboardInterrupt:
        logger.info("⚠️  Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Test failed: {e}", exc_info=True)
        sys.exit(1)


async def _watch_loop(runner, interval_minutes: int, logger, args: argparse.Namespace) -> None:
    """Re-run tests periodically until interrupted."""
    from datetime import datetime

    prev_status = None
    logger.info(f"👀 Watch mode enabled — running every {interval_minutes} min (Ctrl+C to stop)")

    while True:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"\n{'=' * 60}")
        print(f"  Watch run at {timestamp}")
        print(f"{'=' * 60}")

        try:
            report = await runner.run()
        except Exception as e:
            logger.error(f"Watch run failed: {e}")
            await asyncio.sleep(interval_minutes * 60)
            continue

        if getattr(args, "open_pr", False):
            _maybe_publish_healing(report, args, logger)

        # Alert on status transitions
        if prev_status and prev_status == "PASSED" and report.overall_status != "PASSED":
            print(f"\a⚠️  Status changed: {prev_status} → {report.overall_status}")

        prev_status = report.overall_status

        logger.info(f"Next run in {interval_minutes} minutes...")
        await asyncio.sleep(interval_minutes * 60)


def _maybe_publish_healing(report, args: argparse.Namespace, logger) -> None:
    """Publish auto-heal results as a PR when requested.

    Runs only when the test-runner actually healed something. Any failure is
    logged but never changes the doc-test exit code — publishing is a side
    effect of healing, not the source of truth.
    """
    healing = getattr(report, "healing_report", None)
    if healing is None or getattr(healing, "total_healed", 0) == 0:
        logger.info("No healed steps to publish; skipping PR creation.")
        return

    from src.docs.healing_publisher import (
        HealingPublishError,
        PublishConfig,
        publish_healing,
    )

    doc_path = Path(args.doc).resolve()
    repo_root = _find_repo_root(doc_path)
    if repo_root is None:
        logger.error(
            "Could not locate a git repository for %s; skipping PR creation.",
            doc_path,
        )
        return

    html_report_path = Path(args.output) / "test_report.html"
    config = PublishConfig(
        repo_root=repo_root,
        doc_path=doc_path,
        base_branch=args.pr_base_branch,
        remote=args.pr_remote,
        dry_run=args.pr_dry_run,
    )

    try:
        result = publish_healing(
            healing_report=healing,
            doc_test_report=report,
            html_report_path=html_report_path if html_report_path.exists() else None,
            config=config,
        )
    except HealingPublishError as exc:
        logger.error("Failed to publish healing PR: %s", exc)
        return

    if result.skipped_reason == "no-op":
        logger.info("Publisher: nothing to publish.")
    elif result.skipped_reason == "dry-run":
        logger.info(
            "Publisher (dry-run): branch %s committed locally (sha=%s). "
            "Push and PR creation were skipped.",
            result.branch_name, result.commit_sha,
        )
    else:
        logger.info(
            "Publisher: opened PR %s on branch %s (sha=%s).",
            result.pr_url, result.branch_name, result.commit_sha,
        )


def _find_repo_root(start: Path) -> Optional[Path]:
    """Walk up from `start` until a `.git` directory is found."""
    for parent in [start, *start.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _handle_report_exit(report, logger) -> None:
    """Handle exit codes based on report status."""
    if report.overall_status == "PASSED":
        logger.info("✅ All documentation steps are up to date!")
    elif report.overall_status == "DRIFT_DETECTED":
        logger.warning(
            f"⚠️  UI drift detected in steps: {report.steps_outdated}"
        )
        sys.exit(2)  # exit code 2 = drift detected
    else:
        logger.error(
            f"❌ Broken steps: {report.steps_broken}"
        )
        sys.exit(1)


def cli() -> None:
    """Entry point for pyproject.toml scripts."""
    raw_args = parse_args()

    if raw_args.command == "run":
        try:
            args = resolve_run_args(raw_args)
        except RunConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(2)
    else:
        args = raw_args

    setup_logging(args.verbose)

    if args.command == "run":
        asyncio.run(async_main_run(args))
    elif args.command == "test":
        asyncio.run(async_main_test(args))
    elif args.command == "api":
        import uvicorn

        logging.getLogger("main").info("🚀 Starting FastAPI Server on port %s", args.port)
        uvicorn.run("src.api.server:app", host=args.host, port=args.port, reload=True)


if __name__ == "__main__":
    cli()
