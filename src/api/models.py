from typing import Optional
from pydantic import BaseModel, Field

class RunRequest(BaseModel):
    run_id: Optional[str] = Field(
        None,
        description="Optional run id. If provided and a checkpoint exists, execution resumes from it.",
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    goal: str = Field(..., description="The documentation goal to execute.")
    url: str = Field(..., description="The starting URL for the documentation run.")
    model: str = Field("gpt-4o-mini", description="Model to use for planning/critic.")
    annotation_model: Optional[str] = Field(None, description="Model to use for screenshots.")
    language: Optional[str] = Field(None, description="Language for the output document.")
    title: Optional[str] = Field(None, description="H1 title for the generated document.")
    headless: bool = Field(True, description="Whether to run Chrome in headless mode.")
    cookies_file: Optional[str] = Field(None, description="JSON file path with session cookies.")

class RunStatusResponse(BaseModel):
    run_id: str
    status: str
    goal: str
    url: str
    executed_steps: int
    total_planned_steps: int
    last_known_url: Optional[str]
    error: Optional[str] = None
