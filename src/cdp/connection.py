"""
CDP WebSocket Connection Manager.

Handles raw JSON-RPC communication with Chrome DevTools Protocol over WebSocket.
Auto-incrementing message IDs, session-aware messaging, and event listening.
"""

import asyncio
import json
import logging
from typing import Any, Optional

import websockets
from websockets.asyncio.client import ClientConnection

logger = logging.getLogger(__name__)


class CDPConnection:
    """Low-level CDP WebSocket connection with auto-incrementing IDs."""

    def __init__(self):
        self._ws: Optional[ClientConnection] = None
        self._msg_id: int = 0
        self._session_id: Optional[str] = None
        self._pending: dict[int, asyncio.Future] = {}
        self._events: asyncio.Queue = asyncio.Queue()
        self._recv_task: Optional[asyncio.Task] = None

    async def connect(self, ws_url: str) -> None:
        """Establish WebSocket connection to CDP endpoint."""
        logger.info(f"Connecting to {ws_url}")
        self._ws = await websockets.connect(ws_url, max_size=50 * 1024 * 1024)
        self._recv_task = asyncio.create_task(self._recv_loop())
        logger.info("CDP connection established")

    async def _recv_loop(self) -> None:
        """Background loop that routes incoming messages to pending futures or event queue."""
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                msg_id = msg.get("id")

                if msg_id is not None and msg_id in self._pending:
                    # Response to a sent command
                    self._pending[msg_id].set_result(msg)
                    del self._pending[msg_id]
                elif "method" in msg:
                    # Monitor Target auto-attach to heal dead sessions
                    if msg["method"] == "Target.attachedToTarget":
                        target_info = msg.get("params", {}).get("targetInfo", {})
                        if target_info.get("type") == "page":
                            new_session = msg.get("params", {}).get("sessionId")
                            if new_session:
                                logger.debug(f"Auto-attached to new page target: {target_info.get('targetId')}, session: {new_session}")
                                self._session_id = new_session
                                
                    # CDP event (e.g., Page.loadEventFired)
                    await self._events.put(msg)
                else:
                    # Unmatched message — could be a response to an unknown ID
                    logger.debug(f"Unmatched CDP message: {json.dumps(msg)[:200]}")
        except websockets.exceptions.ConnectionClosed:
            logger.warning("CDP WebSocket connection closed")
        except Exception as e:
            logger.error(f"CDP recv loop error: {e}")

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def send(
        self,
        method: str,
        params: Optional[dict] = None,
        session_id: Optional[str] = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """
        Send a CDP command and wait for its response.

        Args:
            method: CDP method name (e.g., 'Page.navigate')
            params: Optional parameters dict
            session_id: Optional session ID for target-scoped commands
            timeout: Max seconds to wait for response

        Returns:
            The CDP response dict (contains 'result' or 'error')
        """
        if self._ws is None:
            raise RuntimeError("Not connected. Call connect() first.")

        msg_id = self._next_id()
        message: dict[str, Any] = {"id": msg_id, "method": method}

        if params:
            message["params"] = params
        if session_id:
            message["sessionId"] = session_id
        elif self._session_id:
            message["sessionId"] = self._session_id

        # Create a future for this response
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending[msg_id] = future

        logger.debug(f"CDP send [{msg_id}]: {method}")
        await self._ws.send(json.dumps(message))

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise TimeoutError(f"CDP command {method} (id={msg_id}) timed out after {timeout}s")

        if "error" in result:
            err = result["error"]
            raise RuntimeError(f"CDP error [{err.get('code')}]: {err.get('message')}")

        return result.get("result", {})

    async def wait_for_event(
        self, event_name: str, timeout: float = 30.0
    ) -> dict[str, Any]:
        """
        Wait for a specific CDP event.

        Non-matching events are buffered and re-queued after the target
        event is found (or on timeout), so they are not lost.

        Args:
            event_name: Event method name (e.g., 'Page.loadEventFired')
            timeout: Max seconds to wait

        Returns:
            The event params dict
        """
        deadline = asyncio.get_event_loop().time() + timeout
        stashed: list[dict[str, Any]] = []

        try:
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise TimeoutError(f"Timed out waiting for event {event_name}")
                try:
                    event = await asyncio.wait_for(self._events.get(), timeout=remaining)
                    if event.get("method") == event_name:
                        return event.get("params", {})
                    # Buffer non-matching events for re-queue
                    stashed.append(event)
                except asyncio.TimeoutError:
                    raise TimeoutError(f"Timed out waiting for event {event_name}")
        finally:
            # Always re-queue stashed events so they are not lost
            for ev in stashed:
                await self._events.put(ev)

    async def attach_to_page(self) -> str:
        """
        Find the first page target and attach to it.

        Returns:
            The sessionId for the attached target.
        """
        targets = await self.send("Target.getTargets")
        page_targets = [
            t for t in targets.get("targetInfos", []) if t["type"] == "page"
        ]
        if not page_targets:
            raise RuntimeError("No page target found. Is a tab open?")

        target_id = page_targets[0]["targetId"]
        logger.info(f"Attaching to page target: {target_id}")

        # Attach to the initial target
        result = await self.send(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
        )
        self._session_id = result["sessionId"]
        logger.info(f"Attached with sessionId: {self._session_id}")
        
        # Turn on auto-attach to handle cross-origin process swaps (healing)
        await self.send(
            "Target.setAutoAttach",
            {
                "autoAttach": True, 
                "waitForDebuggerOnStart": False, 
                "flatten": True
            },
        )
        logger.debug("Enabled Target.setAutoAttach for session resilience")
        
        return self._session_id

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    async def close(self) -> None:
        """Close the WebSocket connection and cancel background tasks."""
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("CDP connection closed")
