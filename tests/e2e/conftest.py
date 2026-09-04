"""Slice-1 E2E fixtures: local server, free CDP port, Chrome gate.

- ``e2e_server`` (session): stdlib HTTP server on 127.0.0.1, yields base_url.
- ``cdp_port`` (function): free TCP port per test for Chrome isolation.
- ``require_chrome`` (function, autouse): skips the whole directory when no
  Chrome binary is found, so unit CI stays green without a browser.
"""

from __future__ import annotations

import socket

import pytest

from src.cdp.browser import _find_chrome_binary
from tests.fixtures.e2e_server import start_server, stop_server


@pytest.fixture(scope="session")
def e2e_server():
    server, _thread, base_url = start_server()
    yield base_url
    stop_server(server)


@pytest.fixture()
def cdp_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(autouse=True)
def require_chrome():
    try:
        _find_chrome_binary()
    except FileNotFoundError:
        pytest.skip("Chrome binary not found — skipping real-browser E2E")
