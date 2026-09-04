"""Slice-1/2 E2E fixtures: local servers, free CDP port, Chrome gate.

- ``e2e_server`` (session): stdlib HTTP server on 127.0.0.1, yields base_url.
- ``e2e_peer`` (session): second server = cross-origin peer (different port).
  Wires ``peer_base`` on both server objects so ``/iframe`` can embed the
  peer's ``/xframe``. Yields the peer base URL.
- ``cdp_port`` (function): free TCP port per test for Chrome isolation.
- ``require_chrome`` (function, autouse): skips the whole directory when no
  Chrome binary is found, so unit CI stays green without a browser.
"""

from __future__ import annotations

import socket
import urllib.request

import pytest

from src.cdp.browser import _find_chrome_binary
from tests.fixtures.e2e_server import start_server, stop_server

_SERVERS: dict[str, object] = {}


@pytest.fixture(scope="session")
def e2e_server():
    server, _thread, base_url = start_server()
    _SERVERS[base_url] = server
    try:
        yield base_url
    finally:
        stop_server(server)
        _SERVERS.pop(base_url, None)


@pytest.fixture(scope="session")
def e2e_peer(e2e_server):
    peer, _thread, peer_base = start_server()
    _SERVERS[peer_base] = peer
    try:
        main = _SERVERS[e2e_server]
        main.peer_base = peer_base  # type: ignore[attr-defined]
        peer.peer_base = e2e_server  # type: ignore[attr-defined]
        assert urllib.request.urlopen(peer_base + "/xframe", timeout=5).status == 200
        assert urllib.request.urlopen(e2e_server + "/iframe", timeout=5).status == 200
        yield peer_base
    finally:
        stop_server(peer)
        _SERVERS.pop(peer_base, None)


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
