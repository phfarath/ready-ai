"""
Batch Config Loader — loads YAML or TOML batch configuration files.

Expected format (YAML):
    app_version: "2.3.1"
    git_commit: "abc1234"
    deployed_at: "2026-05-09T14:00:00Z"
    base_url: "https://app.example.com"
    model: "gpt-4o-mini"
    headless: true
    
    flows:
      - goal: "Document login"
        path: "/login"
        run_id: "login-v2.3.1"
        title: "Login Guide"
        language: "en"
      - goal: "Document onboarding"
        path: "/welcome"
        output: "./output/v2.3.1/onboarding"
"""

import logging
from pathlib import Path

from src.api.models import BatchConfig, BatchConfigFlow

logger = logging.getLogger(__name__)


def load_batch_config(path: str | Path) -> BatchConfig:
    """
    Load a batch configuration from a YAML or TOML file.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    suffix = path.suffix.lower()
    
    if suffix in (".yaml", ".yml"):
        return _load_yaml(path)
    elif suffix == ".toml":
        return _load_toml(path)
    else:
        raise ValueError(f"Unsupported config format: {suffix}. Use .yaml, .yml, or .toml")


def _load_yaml(path: Path) -> BatchConfig:
    try:
        import yaml
    except ImportError:
        raise ImportError("PyYAML is required for YAML batch configs. Install with: pip install pyyaml")

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("YAML batch config must contain a top-level mapping")

    return _parse_dict(data)


def _load_toml(path: Path) -> BatchConfig:
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            raise ImportError("tomli is required for TOML batch configs. Install with: pip install tomli")

    data = tomllib.load(path.open("rb"))
    if not isinstance(data, dict):
        raise ValueError("TOML batch config must contain a top-level table")

    return _parse_dict(data)


def _parse_dict(data: dict) -> BatchConfig:
    """Parse a dict into BatchConfig."""
    flows = []
    for flow_data in data.get("flows", []):
        flows.append(BatchConfigFlow(
            goal=flow_data.get("goal", ""),
            path=flow_data.get("path", ""),
            run_id=flow_data.get("run_id"),
            title=flow_data.get("title"),
            language=flow_data.get("language"),
            output=flow_data.get("output"),
        ))

    return BatchConfig(
        app_version=data.get("app_version"),
        git_commit=data.get("git_commit"),
        deployed_at=data.get("deployed_at"),
        base_url=data.get("base_url"),
        model=data.get("model", "gpt-4o-mini"),
        headless=data.get("headless", True),
        cookies_file=data.get("cookies_file"),
        flows=flows,
    )
