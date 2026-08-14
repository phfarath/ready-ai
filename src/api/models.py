from typing import Any, Optional, List
from pydantic import BaseModel, ConfigDict, Field

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


# ─── Declarative Run-Flow Models (READY-AI-T-4) ──────────────────────
# Docs-independent run mode: YAML/JSON flow documents with actions,
# expectations (asserts), extractions and retries, producing a structured
# JSON result. The flow path never instantiates DocRenderer and never
# requires screenshots or visual annotation.

class FlowAction(BaseModel):
    """A single declarative action dispatched via the executor's CDP core."""
    model_config = ConfigDict(extra="allow")

    action: str = Field(
        ...,
        description=(
            "Executor action type: click, click_text, type, press_key, "
            "navigate, scroll, scroll_to, wait, observe"
        ),
    )
    selector: Optional[str] = Field(None, description="CSS selector for the action target")
    text: Optional[str] = Field(None, description="Text to type (type) or match (click_text)")
    url: Optional[str] = Field(None, description="Target URL for navigate")
    key: Optional[str] = Field(None, description="Key to press for press_key")
    direction: Optional[str] = Field(None, description="Scroll direction (up/down)")
    value: Optional[str] = Field(None, description="Generic value for future action types")
    retries: Optional[int] = Field(
        None,
        description="Per-action retry budget; falls back to the step/flow default",
    )


class FlowAssertion(BaseModel):
    """A declarative expectation evaluated after a step's actions."""
    type: str = Field(
        ...,
        description=(
            "url_contains | url_equals | not_url_contains | element_present | "
            "element_missing | element_visible | text_contains | text_equals | "
            "attribute_equals"
        ),
    )
    expected: Any = Field(None, description="Expected value for the assertion")
    selector: Optional[str] = Field(
        None,
        description="CSS selector (element/text/attribute assertions)",
    )
    attribute: Optional[str] = Field(None, description="Attribute name for attribute_equals")
    message: Optional[str] = Field(None, description="Optional human-readable failure message")


class FlowExtraction(BaseModel):
    """A declarative data extraction performed after a step's actions."""
    name: str = Field(..., description="Result key for the extracted value")
    selector: str = Field(..., description="CSS selector of the element(s) to read")
    attribute: str = Field(
        "textContent",
        description=(
            "Element property to read (textContent, value, href, checked, ...); "
            "prefix with '@' to read an HTML attribute instead"
        ),
    )
    multiple: bool = Field(
        False,
        description="Collect all matches into a list when true",
    )


class FlowStepSpec(BaseModel):
    """A single declarative step: actions, expectations, and extractions."""
    name: Optional[str] = Field(None, description="Optional step label")
    actions: List[FlowAction] = Field(default_factory=list)
    asserts: List[FlowAssertion] = Field(default_factory=list)
    extract: List[FlowExtraction] = Field(default_factory=list)
    retries: Optional[int] = Field(
        None,
        description="Per-step retry budget; falls back to the flow default",
    )


class FlowSpec(BaseModel):
    """Top-level declarative run-flow document (YAML or JSON)."""
    name: Optional[str] = Field(None, description="Flow name")
    url: str = Field(..., description="Starting URL to navigate to")
    steps: List[FlowStepSpec] = Field(
        default_factory=list,
        min_length=1,
        description="Ordered list of steps to execute",
    )
    retries: int = Field(1, description="Default per-action retry budget")
    headless: bool = Field(True, description="Run Chrome in headless mode")
    run_id: Optional[str] = Field(None, description="Run identifier for result output")
    cookies_file: Optional[str] = Field(None, description="JSON cookies file path")
    username: Optional[str] = Field(None, description="Username for auto-login")
    password: Optional[str] = Field(None, description="Password for auto-login")
    output: Optional[str] = Field(None, description="Output directory for the result JSON")
    model: str = Field("gpt-4o-mini", description="LLM model (used only for credential login)")


# ─── Run-Flow Result Models (READY-AI-T-4) ────────────────────────────

class FlowActionReport(BaseModel):
    """Report of one step action, including its retry accounting."""
    action: str = Field(..., description="Action type")
    params: dict = Field(default_factory=dict, description="Action parameters (sensitive text masked)")
    description: str = Field("", description="Human-readable description from the executor")
    attempts: int = Field(1, description="Total attempts (1 + retries consumed)")
    passed: bool = Field(True, description="Whether the action succeeded within its budget")
    failure_reason: str = Field("", description="Last failure reason when the budget was exhausted")


class FlowAssertionResult(BaseModel):
    """Outcome of a single declarative expectation."""
    type: str
    selector: Optional[str] = None
    expected: Any = None
    actual: Any = None
    passed: bool
    message: str = ""


class FlowExtractionResult(BaseModel):
    """A single extracted value."""
    name: str
    selector: str
    value: Any = None


class FlowStepResult(BaseModel):
    """Structured report for one executed step."""
    index: int
    name: Optional[str] = None
    actions: List[FlowActionReport] = Field(default_factory=list)
    asserts: List[FlowAssertionResult] = Field(default_factory=list)
    extracted: List[FlowExtractionResult] = Field(default_factory=list)
    attempts: int = 1
    status: str = Field("passed", description="passed | failed")
    failure_reason: str = ""


class FlowRunResult(BaseModel):
    """Structured JSON result of an entire run-flow execution."""
    run_id: str
    flow: Optional[str] = None
    url: str
    status: str = Field("passed", description="passed | failed")
    steps: List[FlowStepResult] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)


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
