"""
Chrome Browser Launcher.

Starts Chrome/Chromium with --remote-debugging-port and fetches the
WebSocket debugger URL for CDP connection.
"""

import asyncio
import json
import logging
import os
import platform
import subprocess
import tempfile
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# Env vars read by launch_chrome. We intentionally only allow these to be
# opted into — Docker rootful and similar environments need --no-sandbox,
# and screenshots are easier to compare across runs at a fixed viewport.
ENV_NO_SANDBOX = "CHROME_NO_SANDBOX"
ENV_WINDOW_SIZE = "CHROME_WINDOW_SIZE"
ENV_CHROME_ARGS = "CHROME_ARGS"

# Chrome binary paths by platform
_CHROME_PATHS = {
    "Darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ],
    "Linux": [
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ],
    "Windows": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Chromium\Application\chrome.exe",
    ],
}


def _find_chrome_binary() -> str:
    """Locate Chrome binary for the current platform."""
    # Check env var first
    env_path = os.environ.get("CHROME_PATH")
    if env_path and os.path.exists(env_path):
        return env_path

    system = platform.system()
    candidates = _CHROME_PATHS.get(system, [])

    for path in candidates:
        if os.path.exists(path):
            return path
        # For Linux, try which
        if system == "Linux":
            try:
                result = subprocess.run(
                    ["which", path], capture_output=True, text=True
                )
                if result.returncode == 0:
                    return result.stdout.strip()
            except FileNotFoundError:
                continue

    raise FileNotFoundError(
        f"Chrome not found. Searched: {candidates}. "
        f"Set CHROME_PATH env var to your Chrome/Chromium binary path."
    )


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _parse_window_size(value: str) -> Optional[tuple[int, int]]:
    """Parse 'WxH' or 'W,H' into (width, height). Returns None on bad input."""
    try:
        cleaned = value.replace(",", "x").lower()
        w, h = cleaned.split("x")
        return int(w), int(h)
    except (ValueError, AttributeError):
        logger.warning(f"Invalid CHROME_WINDOW_SIZE={value!r}, expected WxH (e.g. 1920x1080)")
        return None


def launch_chrome(
    port: int = 9222,
    headless: bool = False,
    user_data_dir: Optional[str] = None,
    no_sandbox: Optional[bool] = None,
    window_size: Optional[tuple[int, int]] = None,
    extra_args: Optional[list[str]] = None,
) -> subprocess.Popen:
    """
    Launch Chrome with remote debugging enabled.

    Args:
        port: CDP debugging port
        headless: Run in headless mode
        user_data_dir: Chrome user data directory (uses temp if None)
        no_sandbox: Add --no-sandbox (required when running as root in
            Docker). When None (default), reads CHROME_NO_SANDBOX env.
        window_size: (width, height) passed as --window-size. When None,
            reads CHROME_WINDOW_SIZE env (format: WxH or W,H).
        extra_args: Additional CLI flags appended to the command. May
            also be supplied via CHROME_ARGS (space-separated).

    Returns:
        subprocess.Popen handle for the Chrome process
    """
    chrome_bin = _find_chrome_binary()
    if user_data_dir is None:
        user_data_dir = tempfile.mkdtemp(prefix="ready-ai-chrome-")

    # Resolve opt-in flags from env when not provided explicitly.
    if no_sandbox is None:
        no_sandbox = _truthy(os.environ.get(ENV_NO_SANDBOX, ""))
    if window_size is None:
        env_size = os.environ.get(ENV_WINDOW_SIZE, "")
        window_size = _parse_window_size(env_size) if env_size else None

    args = [
        chrome_bin,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
    ]

    if headless:
        args.append("--headless=new")

    if no_sandbox:
        # Required in Docker rootful and some CI sandboxes. Opt-in
        # because it weakens Chrome's process isolation; only enable
        # when the host environment actually requires it.
        args.append("--no-sandbox")
        logger.debug("Chrome launched with --no-sandbox (env or explicit)")

    if window_size is not None:
        width, height = window_size
        if width > 0 and height > 0:
            args.append(f"--window-size={width},{height}")

    if extra_args:
        args.extend(extra_args)

    env_args = os.environ.get(ENV_CHROME_ARGS, "").strip()
    if env_args:
        args.extend(env_args.split())

    logger.info(f"Launching Chrome: {chrome_bin} on port {port} (headless={headless})")
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    logger.info(f"Chrome PID: {proc.pid}")
    return proc


async def get_ws_url(port: int = 9222, retries: int = 10, delay: float = 1.0) -> str:
    """
    Fetch the WebSocket debugger URL from Chrome's /json/version endpoint.

    Args:
        port: CDP debugging port
        retries: Max retry attempts
        delay: Seconds between retries

    Returns:
        The webSocketDebuggerUrl string
    """
    url = f"http://localhost:{port}/json/version"

    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    data = await resp.json()
                    ws_url = data["webSocketDebuggerUrl"]
                    logger.info(f"Got WS URL: {ws_url}")
                    return ws_url
        except (aiohttp.ClientError, KeyError, json.JSONDecodeError) as e:
            if attempt < retries - 1:
                logger.debug(f"Waiting for Chrome (attempt {attempt + 1}): {e}")
                await asyncio.sleep(delay)
            else:
                raise RuntimeError(
                    f"Could not get Chrome WS URL after {retries} attempts. "
                    f"Is Chrome running with --remote-debugging-port={port}?"
                ) from e
