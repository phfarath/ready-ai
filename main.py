#!/usr/bin/env python3
"""
browser-auto: Agentic browser automation for SaaS documentation.

Uses raw CDP (Chrome DevTools Protocol) + LLM to navigate SaaS UIs,
capture screenshots, and generate annotated Markdown documentation.

Usage:
    python main.py run --goal "Documentar fluxo de login" --url "https://app.example.com"
    python main.py api --port 8000
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()  # Load .env file (API keys etc.)

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.agent.loop import AgenticLoop


def setup_logging(verbose: bool = False) -> None:
    """Configure logging with colored output."""
    level = logging.DEBUG if verbose else logging.INFO

    # Custom formatter with emojis for readability
    fmt = "%(asctime)s │ %(levelname)-8s │ %(name)-20s │ %(message)s"
    datefmt = "%H:%M:%S"

    logging.basicConfig(level=level, format=fmt, datefmt=datefmt)

    # Quiet noisy libraries
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("litellm").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="🤖 Agentic browser automation for SaaS documentation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    subparsers = parser.add_subparsers(dest="command", required=True, help="Sub-commands")
    
    # --- RUN Command ---
    run_parser = subparsers.add_parser("run", help="Run the documentation agent locally")
    
    run_parser.add_argument("--goal", "-g", required=True, help="Documentation goal")
    run_parser.add_argument("--url", "-u", required=True, help="Target SaaS URL")
    run_parser.add_argument("--title", "-t", default=None, help="Document title")
    run_parser.add_argument("--language", "-l", default=None, help="Output language")
    run_parser.add_argument("--model", "-m", default="gpt-4o-mini", help="LLM model (default: gpt-4o-mini)")
    run_parser.add_argument("--output", "-o", default="./output", help="Output directory")
    run_parser.add_argument("--port", "-p", type=int, default=9222, help="Chrome debugging port")
    run_parser.add_argument("--headless", action="store_true", help="Run headless")
    run_parser.add_argument("--max-critic-rounds", type=int, default=2, help="Max critic rounds")
    run_parser.add_argument("--annotation-model", default=None, help="Specific model for vision")
    run_parser.add_argument("--cookies-file", default=None, help="JSON cookies file")
    run_parser.add_argument("--username", default=None, help="Username for auto-login")
    run_parser.add_argument("--password", default=None, help="Password for auto-login")
    run_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose debug logging")

    # --- API Command ---
    api_parser = subparsers.add_parser("api", help="Start the FastAPI server")
    api_parser.add_argument("--port", "-p", type=int, default=8000, help="API server port")
    api_parser.add_argument("--host", default="0.0.0.0", help="API server host")
    api_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose debug logging")

    return parser.parse_args()


async def async_main_run(args: argparse.Namespace) -> None:
    logger = logging.getLogger("main")
    logger.info("🚀 browser-auto — Local CLI Run")
    
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
    )

    try:
        output_path = await loop.run()
        logger.info(f"✅ Documentation complete! Saved to: {output_path}")
    except KeyboardInterrupt:
        logger.info("⚠️  Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Failed: {e}", exc_info=True)
        sys.exit(1)


def cli():
    """Entry point for pyproject.toml scripts."""
    args = parse_args()
    setup_logging(args.verbose)
    
    if args.command == "run":
        asyncio.run(async_main_run(args))
    elif args.command == "api":
        import uvicorn
        logging.getLogger("main").info("🚀 Starting FastAPI Server on port %s", args.port)
        uvicorn.run("src.api.server:app", host=args.host, port=args.port, reload=True)


if __name__ == "__main__":
    cli()
