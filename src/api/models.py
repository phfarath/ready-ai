from typing import Optional, List
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
    app_version: Optional[str] = Field(None, description="Application version being documented.")
    git_commit: Optional[str] = Field(None, description="Git commit hash of the application.")
    deployed_at: Optional[str] = Field(None, description="ISO timestamp when version was deployed.")

class RunStatusResponse(BaseModel):
    run_id: str
    status: str
    goal: str
    url: str
    executed_steps: int
    total_planned_steps: int
    last_known_url: Optional[str]
    error: Optional[str] = None


# ─── Deploy Webhook Models ─────────────────────────────────────────────

class FlowConfig(BaseModel):
    goal: str = Field(..., description="Documentation goal for this flow.")
    path: str = Field(..., description="URL path or full URL to document.")
    run_id: Optional[str] = Field(None, description="Optional run identifier.")
    title: Optional[str] = Field(None, description="Document H1 title.")
    language: Optional[str] = Field(None, description="Output language.")

class DeployWebhookPayload(BaseModel):
    app_version: str = Field(..., description="Application version (e.g., 2.3.1).")
    git_commit: str = Field(..., description="Git commit hash.")
    deployed_at: str = Field(..., description="ISO timestamp of deployment.")
    base_url: str = Field(..., description="Base URL of the deployed application.")
    flows: List[FlowConfig] = Field(..., description="Flows to document for this release.")
    model: str = Field("gpt-4o-mini", description="LLM model to use.")
    headless: bool = Field(True, description="Run Chrome headless.")
    cookies_file: Optional[str] = Field(None, description="Session cookies file path.")

class BatchRunResponse(BaseModel):
    batch_id: str = Field(..., description="Unique identifier for this batch.")
    total_flows: int
    accepted: int
    rejected: int
    run_ids: List[str]
    status: str = "ACCEPTED"

class BatchStatusResponse(BaseModel):
    batch_id: str
    total_flows: int
    completed: int
    failed: int
    running: int
    pending: int
    statuses: List[RunStatusResponse]


# ─── Batch Config (YAML) Model ─────────────────────────────────────────

class BatchConfigFlow(BaseModel):
    goal: str
    path: str
    run_id: Optional[str] = None
    title: Optional[str] = None
    language: Optional[str] = None
    output: Optional[str] = None

class BatchConfig(BaseModel):
    app_version: Optional[str] = None
    git_commit: Optional[str] = None
    deployed_at: Optional[str] = None
    base_url: Optional[str] = None
    model: str = "gpt-4o-mini"
    headless: bool = True
    cookies_file: Optional[str] = None
    flows: List[BatchConfigFlow] = Field(default_factory=list)

# ─── Export Models ─────────────────────────────────────────────────────────

class ExportRequest(BaseModel):
    """Request to export a completed run to a documentation format."""
    format: str = Field(..., description="Export format: markdown, docusaurus, nextra, mintlify, starlight")
    output_dir: Optional[str] = Field(None, description="Custom output directory. Defaults to ./output/{run_id}/export/{format}/")

class ExportResponse(BaseModel):
    """Result of an export operation."""
    run_id: str
    format: str
    output_dir: str
    files_created: list[str]
    success: bool = True


# ─── List Models ──────────────────────────────────────────────────────────

class RunListItem(BaseModel):
    """Summary item for listing runs."""
    run_id: str
    status: str
    goal: str
    url: str
    executed_steps: int
    total_planned_steps: int
    app_version: Optional[str] = None
    git_commit: Optional[str] = None
    deployed_at: Optional[str] = None
    created_at: Optional[str] = None

class RunListResponse(BaseModel):
    """Response for GET /runs."""
    total: int
    runs: list[RunListItem]

class DiffResponse(BaseModel):
    """Response for GET /runs/{run_id}/diff."""
    run_id: str
    previous_version: Optional[str] = None
    current_version: Optional[str] = None
    changelog: str
    diff_json: dict

class DocsSetItem(BaseModel):
    """Item in the documentation sets list."""
    run_id: str
    version: Optional[str] = None
    goal: str
    url: str
    status: str
    files: list[str]
    generated_at: Optional[str] = None

class DocsListResponse(BaseModel):
    """Response for GET /docs."""
    total: int
    docs: list[DocsSetItem]

class DocsVersionStatus(BaseModel):
    """Health status for a specific documentation version."""
    version: str
    run_id: str
    status: str
    steps_count: int
    screenshots_count: int
    has_manifest: bool
    has_metrics: bool
    last_tested: Optional[str] = None
    test_status: Optional[str] = None
