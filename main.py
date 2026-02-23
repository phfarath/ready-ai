#!/usr/bin/env python3
"""
browser-auto: Agentic browser automation for SaaS documentation.

Uses raw CDP (Chrome DevTools Protocol) + LLM to navigate SaaS UIs,
capture screenshots, and generate annotated Markdown documentation.

Usage:
    python main.py --goal "Documentar fluxo de login" \
                   --url "https://app.example.com" \
                   --model "gpt-4o-mini" \
                   --output "./output"
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
        epilog="""
Examples:
  # Document a login flow
  python main.py --goal "Documentar fluxo de login" --url "https://app.example.com"
  
  # Use Claude for planning, cheap model for annotations  
  python main.py --goal "Document sign-up" --url "https://app.com" --model "claude-sonnet-4-20250514" --annotation-model "gpt-4o-mini"
  
  # With authentication via cookies
  python main.py --goal "Document dashboard" --url "https://app.com" --cookies-file ./cookies.json
  
  # With username/password login
  python main.py --goal "Document settings" --url "https://app.com" --username user@email.com --password mysecret
        """,
    )

    parser.add_argument(
        "--goal", "-g",
        required=True,
        help="Documentation goal (e.g., 'Documentar fluxo de login')",
    )
    parser.add_argument(
        "--url", "-u",
        required=True,
        help="Target SaaS URL to document",
    )
    parser.add_argument(
        "--title", "-t",
        default=None,
        help="Document title (defaults to --goal if not set)",
    )
    parser.add_argument(
        "--model", "-m",
        default="gpt-4o-mini",
        help="LLM model to use (default: gpt-4o-mini). Supports any LiteLLM model.",
    )
    parser.add_argument(
        "--output", "-o",
        default="./output",
        help="Output directory for generated docs (default: ./output)",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=9222,
        help="Chrome remote debugging port (default: 9222)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chrome in headless mode",
    )
    parser.add_argument(
        "--max-critic-rounds",
        type=int,
        default=2,
        help="Maximum critic review rounds (default: 2)",
    )
    parser.add_argument(
        "--annotation-model",
        default=None,
        help="Separate LLM model for screenshot annotations (default: same as --model). Use a cheaper model to reduce cost.",
    )
    parser.add_argument(
        "--cookies-file",
        default=None,
        help="Path to a JSON cookies file for session auth (e.g., exported via EditThisCookie)",
    )
    parser.add_argument(
        "--username",
        default=None,
        help="Username/email for auto-login",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Password for auto-login",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose debug logging",
    )

    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    setup_logging(args.verbose)

    logger = logging.getLogger("main")
    logger.info("🚀 browser-auto — Agentic SaaS Documentation Generator")
    logger.info(f"   Goal:  {args.goal}")
    logger.info(f"   URL:   {args.url}")
    logger.info(f"   Model: {args.model}")
    logger.info(f"   Output: {args.output}")

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
    asyncio.run(async_main())


if __name__ == "__main__":
    cli()
