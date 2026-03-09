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


def launch_chrome(
    port: int = 9222,
    headless: bool = False,
    user_data_dir: Optional[str] = None,
) -> subprocess.Popen:
    """
    Launch Chrome with remote debugging enabled.

    Args:
        port: CDP debugging port
        headless: Run in headless mode
        user_data_dir: Chrome user data directory (uses temp if None)

    Returns:
        subprocess.Popen handle for the Chrome process
    """
    chrome_bin = _find_chrome_binary()
    if user_data_dir is None:
        user_data_dir = tempfile.mkdtemp(prefix="ready-ai-chrome-")

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

    logger.info(f"Launching Chrome: {chrome_bin} on port {port}")
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
