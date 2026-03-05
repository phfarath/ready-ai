"""
Manager to run AgenticLoop inside an asyncio background task.
Handles status tracking and lifecycle.
"""
import asyncio
import logging
import uuid
from pathlib import Path
from typing import Dict, Optional

from src.api.models import RunRequest, RunStatusResponse
from src.agent.loop import AgenticLoop
from src.agent.state import RunState

logger = logging.getLogger(__name__)

class RunManager:
    """Singleton responsible for managing active documentation runs."""
    
    _runs: Dict[str, asyncio.Task] = {}
    _states: Dict[str, RunState] = {}
    _run_ports: Dict[str, int] = {}
    _port_pool: Optional[asyncio.Queue[int]] = None

    _PORT_START = 9300
    _PORT_END = 9399

    @classmethod
    def _state_path(cls, run_id: str) -> Path:
        return Path("./output") / run_id / f"{run_id}_state.json"

    @classmethod
    def _ensure_port_pool(cls) -> None:
        if cls._port_pool is not None:
            return

        cls._port_pool = asyncio.Queue()
        for port in range(cls._PORT_START, cls._PORT_END + 1):
            cls._port_pool.put_nowait(port)

    @classmethod
    async def _acquire_port(cls) -> int:
        cls._ensure_port_pool()
        assert cls._port_pool is not None
        try:
            return cls._port_pool.get_nowait()
        except asyncio.QueueEmpty as exc:
            raise RuntimeError("No available browser ports in pool.") from exc

    @classmethod
    async def _release_port(cls, port: int) -> None:
        cls._ensure_port_pool()
        assert cls._port_pool is not None
        cls._port_pool.put_nowait(port)
    
    @classmethod
    async def start_run(cls, req: RunRequest) -> str:
        """Initialize and start a new background run."""
        run_id = req.run_id or str(uuid.uuid4())
        existing_task = cls._runs.get(run_id)
        if existing_task and not existing_task.done():
            raise ValueError(f"Run '{run_id}' is already active.")
        if existing_task and existing_task.done():
            cls._runs.pop(run_id, None)

        output_dir = Path("./output") / run_id
        resume_path = cls._state_path(run_id)
        resume_from = str(resume_path) if resume_path.exists() else None

        port = await cls._acquire_port()
        
        try:
            loop = AgenticLoop(
                goal=req.goal,
                url=req.url,
                model=req.model,
                annotation_model=req.annotation_model,
                language=req.language,
                title=req.title,
                headless=req.headless,
                cookies_file=req.cookies_file,
                run_id=run_id,
                output_dir=str(output_dir),
                resume_from=resume_from,
                port=port,
            )
        except Exception:
            await cls._release_port(port)
            raise
        
        # Store state ref (which will be updated by AgenticLoop internally)
        cls._states[run_id] = loop._state
        cls._run_ports[run_id] = port
        
        # Create background task
        task = asyncio.create_task(cls._execute(run_id, loop))
        cls._runs[run_id] = task
        
        return run_id
        
    @classmethod
    async def _execute(cls, run_id: str, loop: AgenticLoop) -> None:
        """Run the loop inside a task wrapper."""
        try:
            await loop.run()
        except Exception as e:
            logger.error(f"Run {run_id} failed: {e}")
        finally:
            if run_id in cls._runs:
                del cls._runs[run_id]
            port = cls._run_ports.pop(run_id, None)
            if port is not None:
                await cls._release_port(port)
                
    @classmethod
    def get_status(cls, run_id: str) -> Optional[RunStatusResponse]:
        """Fetch the current status of a run ID."""
        if run_id not in cls._states:
            # Maybe it exists on disk? Try loading it.
            state = RunState.from_file(cls._state_path(run_id))
            if not state:
                return None
            cls._states[run_id] = state
            
        state = cls._states[run_id]
        
        return RunStatusResponse(
            run_id=state.run_id,
            status=state.status,
            goal=state.goal,
            url=state.url,
            executed_steps=len(state.executed_results),
            total_planned_steps=len(state.planned_steps),
            last_known_url=state.last_known_url,
        )
