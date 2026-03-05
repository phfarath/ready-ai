"""
Manager to run AgenticLoop inside an asyncio background task.
Handles status tracking and lifecycle.
"""
import asyncio
import logging
import uuid
from typing import Dict, Optional

from src.api.models import RunRequest, RunStatusResponse
from src.agent.loop import AgenticLoop
from src.agent.state import RunState

logger = logging.getLogger(__name__)

class RunManager:
    """Singleton responsible for managing active documentation runs."""
    
    _runs: Dict[str, asyncio.Task] = {}
    _states: Dict[str, RunState] = {}
    
    @classmethod
    def start_run(cls, req: RunRequest) -> str:
        """Initialize and start a new background run."""
        run_id = str(uuid.uuid4())
        
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
        )
        
        # Store state ref (which will be updated by AgenticLoop internally)
        cls._states[run_id] = loop._state
        
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
                
    @classmethod
    def get_status(cls, run_id: str) -> Optional[RunStatusResponse]:
        """Fetch the current status of a run ID."""
        if run_id not in cls._states:
            # Maybe it exists on disk? Try loading it.
            # In a real system, you'd specify output_dir centrally.
            state = RunState.from_file(f"./output/{run_id}_state.json")
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
