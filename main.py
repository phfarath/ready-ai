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
import json
import logging
import sys
from pathlib import Path
from typing import Any

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
    "app_version": None,
    "git_commit": None,
    "deployed_at": None,
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
    run_parser.add_argument("--app-version", default=argparse.SUPPRESS, help="Application version (e.g., 2.3.1)")
    run_parser.add_argument("--git-commit", default=argparse.SUPPRESS, help="Git commit hash")
    run_parser.add_argument("--deployed-at", default=argparse.SUPPRESS, help="Deployment ISO timestamp")

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
    test_parser.add_argument("--open-pr", action="store_true", help="After auto-heal, commit changes and open a PR (implies --auto-heal)")
    test_parser.add_argument("--pr-base-branch", default="dev", help="Base branch for the auto-heal PR (default: dev)")
    test_parser.add_argument("--pr-remote", default="origin", help="Git remote used to push the auto-heal branch (default: origin)")
    test_parser.add_argument("--pr-dry-run", action="store_true", help="Run git steps locally but skip push and PR creation")

    api_parser = subparsers.add_parser("api", help="Start the FastAPI server")
    api_parser.add_argument("--port", "-p", type=int, default=8000, help="API server port")
    api_parser.add_argument("--host", default="0.0.0.0", help="API server host")
    api_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose debug logging")

    # --- BATCH Command ---
    batch_parser = subparsers.add_parser("batch", help="Run multiple documentation flows from a config file")
    batch_parser.add_argument("--config", "-c", required=True, help="YAML or TOML batch config file")
    batch_parser.add_argument("--output", "-o", default="./output", help="Output directory (default: ./output)")
    batch_parser.add_argument("--headless", action="store_true", help="Run Chrome headless")
    batch_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose debug logging")

    # --- RUN-FLOW Command (READY-AI-T-4) ---
    # Docs-independent declarative execution: flow YAML/JSON with actions,
    # asserts, extractions and retries → structured JSON result. Never
    # instantiates DocRenderer and does not require screenshots/annotation.
    flow_parser = subparsers.add_parser(
        "run-flow",
        help="Run a declarative flow (YAML/JSON) and output a structured JSON result",
    )
    flow_parser.add_argument("--config", "-c", required=True, help="Flow YAML or JSON file")
    flow_parser.add_argument("--output", "-o", default="./output", help="Output directory (default: ./output)")
    flow_parser.add_argument("--headless", action="store_true", default=False, help="Run Chrome headless")
    flow_parser.add_argument("--port", "-p", type=int, default=9222, help="Chrome debugging port")
    flow_parser.add_argument(
        "--model", "-m", default=None, help="LLM model (only used for credential auto-login)"
    )
    flow_parser.add_argument("--cookies-file", default=None, help="JSON cookies file")
    flow_parser.add_argument("--username", default=None, help="Username for auto-login")
    flow_parser.add_argument("--password", default=None, help="Password for auto-login")
    flow_parser.add_argument("--run-id", default=None, help="Run identifier for result output")
    flow_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose debug logging")

    # --- EXPORT Command ---
    export_parser = subparsers.add_parser("export", help="Export generated docs to a static-site format")
    export_parser.add_argument("--run-id", "-r", required=True, help="Run ID to export")
    export_parser.add_argument("--format", "-f", required=True, choices=["markdown", "docusaurus", "nextra", "mintlify", "starlight"], help="Export format")
    export_parser.add_argument("--output-dir", "-o", default=argparse.SUPPRESS, help="Custom output directory")
    export_parser.add_argument("--verbose", "-v", action="store_true", default=argparse.SUPPRESS, help="Verbose debug logging")

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
        app_version=getattr(args, "app_version", None),
        git_commit=getattr(args, "git_commit", None),
        deployed_at=getattr(args, "deployed_at", None),
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


async def _watch_loop(runner, interval_minutes: int, logger, args: argparse.Namespace = None) -> None:
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

        # Alert on status transitions
        if prev_status and prev_status == "PASSED" and report.overall_status != "PASSED":
            print(f"\a⚠️  Status changed: {prev_status} → {report.overall_status}")

        prev_status = report.overall_status

        logger.info(f"Next run in {interval_minutes} minutes...")
        await asyncio.sleep(interval_minutes * 60)


def _handle_report_exit(report, logger) -> None:
    """Handle exit codes based on report status."""
    if report.overall_status == "PASSED":
        logger.info("✅ All documentation steps are up to date!")
    elif report.overall_status == "DRIFT_DETECTED":
        logger.warning(
            f"⚠️  UI drift detected in steps: {report.steps_outdated}"
        )
        sys.exit(2)  # exit code 2 = drift detected


def _maybe_publish_healing(report, args: argparse.Namespace, logger) -> None:
    """If auto-heal produced changes, commit and open a PR."""
    from src.docs.healing_publisher import publish_healing, PublishConfig

    healing_report = getattr(report, "healing_report", None)
    if healing_report is None:
        logger.info("⏭️  Skipped: no healing report produced")
        return

    doc_path = Path(args.doc).resolve()
    # Walk up from doc directory to find .git
    repo_root = doc_path
    while repo_root.parent != repo_root:
        if (repo_root / ".git").exists():
            break
        repo_root = repo_root.parent
    cfg = PublishConfig(
        repo_root=repo_root,
        doc_path=doc_path,
        base_branch=getattr(args, "pr_base_branch", "dev"),
        remote=getattr(args, "pr_remote", "origin"),
        dry_run=getattr(args, "pr_dry_run", False),
    )

    html_report_path = Path(args.output) / "test_report.html"
    try:
        result = publish_healing(healing_report, report, html_report_path, cfg)
        if result.pr_url:
            logger.info(f"✅ PR created: {result.pr_url}")
        elif result.skipped_reason:
            logger.info(f"⏭️  Skipped: {result.skipped_reason}")
        else:
            logger.info("✅ Healing committed locally (dry run).")
    except Exception as exc:
        logger.error(f"❌ PR creation failed: {exc}")


async def async_main_batch(args: argparse.Namespace) -> None:
    """Run multiple documentation flows from a batch config file."""
    import uuid
    from src.api.batch_loader import load_batch_config
    from src.api.manager import RunManager

    logger = logging.getLogger("main")
    logger.info("📦 ready-ai — Batch Runner")
    logger.info(f"Config: {args.config}")

    try:
        config = load_batch_config(args.config)
        logger.info(f"Loaded {len(config.flows)} flow(s) from config")

        batch_id = f"batch-{uuid.uuid4().hex[:8]}"
        result = await RunManager.start_batch(config, batch_id)

        logger.info(f"Batch {batch_id} started: {result['accepted']} accepted, {result['rejected']} rejected")
        logger.info(f"Run IDs: {result['run_ids']}")

        # Poll until complete (optional — can skip for fire-and-forget)
        # For CLI, we wait for all to complete
        logger.info("Waiting for batch to complete...")
        while True:
            status = RunManager.get_batch_status(batch_id)
            if not status:
                break

            completed = status["completed"]
            failed = status["failed"]
            running = status["running"]
            total = status["total_flows"]

            logger.info(f"Progress: {completed}/{total} completed, {failed} failed, {running} running")

            if completed + failed == total:
                break

            await asyncio.sleep(5)

        status = RunManager.get_batch_status(batch_id)
        if status:
            logger.info(
                f"✅ Batch complete: {status['completed']}/{total} succeeded, "
                f"{status['failed']} failed"
            )

    except Exception as e:
        logger.error(f"❌ Batch failed: {e}", exc_info=True)
        sys.exit(1)


async def async_main_run_flow(args: argparse.Namespace) -> None:
    """Run a declarative flow (YAML/JSON) and print a structured JSON result.

    Docs-independent mode (READY-AI-T-4): actions, asserts, extractions and
    retries are executed through the same agent/executor core as the docs
    pipeline, but no DocRenderer or screenshots are involved. The result
    JSON is printed to stdout; exit code 0 means the whole flow passed.
    """
    import uuid
    from src.api.batch_loader import load_flow_config
    from src.agent.loop import AgenticLoop

    logger = logging.getLogger("main")
    logger.info("🏃 ready-ai — Declarative Run-Flow")

    flow = load_flow_config(args.config)
    run_id = args.run_id or flow.run_id or f"flow-{uuid.uuid4().hex[:8]}"

    loop = AgenticLoop(
        goal=flow.name or "run-flow",
        url=flow.url,
        model=args.model or flow.model,
        output_dir=args.output,
        port=args.port,
        headless=args.headless or flow.headless,
        cookies_file=args.cookies_file or flow.cookies_file,
        username=args.username or flow.username,
        password=args.password or flow.password,
        run_id=run_id,
    )

    try:
        result = await loop.run_flow(flow)
    except KeyboardInterrupt:
        logger.info("⚠️  Interrupted by user")
        sys.exit(1)
    except Exception as exc:
        logger.error("❌ Run-flow failed: %s", exc, exc_info=True)
        sys.exit(1)

    print(json.dumps(result, indent=2, default=str))

    summary = result["summary"]
    if result["status"] == "passed":
        logger.info(
            "✅ Run-flow passed (%s/%s steps)",
            summary["steps_passed"],
            summary["steps_total"],
        )
    else:
        logger.warning(
            "❌ Run-flow failed (%s/%s steps; %s action(s), %s assert(s))",
            summary["steps_failed"],
            summary["steps_total"],
            summary["actions_failed"],
            summary["asserts_failed"],
        )
        sys.exit(1)


async def async_main_export(args: argparse.Namespace) -> None:
    """Export a completed run to a documentation format."""
    from pathlib import Path
    from src.docs.export import export_docs, SUPPORTED_FORMATS

    logger = logging.getLogger("main")
    run_output_dir = Path("./output") / args.run_id
    doc_path = run_output_dir / "docs.md"

    if not doc_path.exists():
        logger.error("❌ docs.md not found for run %s", args.run_id)
        sys.exit(1)

    format_name = args.format.lower()
    if format_name not in SUPPORTED_FORMATS:
        logger.error("❌ Unknown format '%s'. Supported: %s", format_name, ", ".join(SUPPORTED_FORMATS))
        sys.exit(2)

    if hasattr(args, 'output_dir') and args.output_dir:
        export_output_dir = Path(args.output_dir)
    else:
        export_output_dir = Path("./output") / args.run_id / "export" / format_name

    try:
        result = export_docs(
            doc_path=doc_path,
            format=format_name,
            output_dir=export_output_dir,
            screenshots_dir=run_output_dir / "screenshots",
        )
        logger.info("✅ Exported %s docs to %s", format_name, result.output_dir)
        logger.info("   Files created: %d", len(result.files_created))
        for f in result.files_created:
            logger.info("   - %s", f)
    except Exception as e:
        logger.error("❌ Export failed: %s", e)
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
    elif args.command == "batch":
        asyncio.run(async_main_batch(args))
    elif args.command == "run-flow":
        asyncio.run(async_main_run_flow(args))
    elif args.command == "export":
        asyncio.run(async_main_export(args))


if __name__ == "__main__":
    cli()
