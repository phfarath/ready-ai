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
    elif args.command == "api":
        import uvicorn

        logging.getLogger("main").info("🚀 Starting FastAPI Server on port %s", args.port)
        uvicorn.run("src.api.server:app", host=args.host, port=args.port, reload=True)


if __name__ == "__main__":
    cli()
