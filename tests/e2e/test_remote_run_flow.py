"""API B parte 2 e2e: run remoto real (Chrome server-side).

Boots the real FastAPI app and executes a public ``Flow`` through
``ReadyAI(base_url=...)`` against the fixture SPA. Skipped without a
Chrome binary (server-side flow needs a real browser).
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from ready_ai import Flow, FlowAction, FlowAssertion, FlowStep, ReadyAI, RunResult

pytestmark = pytest.mark.e2e


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.asyncio
async def test_remote_run_flow_passed(e2e_server):
    import uvicorn

    from src.api.server import app

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            lifespan="off",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 15
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started, "uvicorn nao subiu a tempo"

        flow = Flow(
            url=f"{e2e_server}/spa",
            steps=[
                FlowStep(
                    name="Go to products",
                    actions=[FlowAction(action="click", selector="#nav-products")],
                    asserts=[
                        FlowAssertion(
                            type="url_contains",
                            expected="/spa/products",
                        )
                    ],
                )
            ],
        )
        ai = ReadyAI(base_url=f"http://127.0.0.1:{port}")
        result = await ai.run(flow)
        assert isinstance(result, RunResult)
        assert result.status == "passed", result
        assert result.steps and result.steps[0].status == "passed"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
