"""The deterministic engine imports without LiteLLM (READY-AI-T-MVP-02).

A subprocess blocks ``litellm`` even when the extra is installed in this
interpreter, so the test covers the base-install import path.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


_PROBE = textwrap.dedent(
    """
    import sys

    sys.modules["litellm"] = None
    sys.modules["openai"] = None
    sys.modules["openai._compat"] = None

    import ready_ai
    from ready_ai._engine.agent.replay import manifest_to_flow_spec, read_manifest
    from ready_ai._engine.llm.client import require_llm
    from ready_ai.cli import _require_llm_extra

    assert isinstance(ready_ai.__version__, str) and ready_ai.__version__
    assert callable(read_manifest) and callable(manifest_to_flow_spec)

    try:
        require_llm()
    except ImportError as exc:
        message = str(exc)
    else:
        raise SystemExit("require_llm() succeeded without litellm")

    assert "pip install 'ready-ai[llm]'" in message, message

    try:
        _require_llm_extra()
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise SystemExit("CLI llm guard did not exit")

    print("NO-LLM-OK")
    """
)


def test_deterministic_imports_without_litellm():
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "NO-LLM-OK" in proc.stdout
    assert "openai._compat is unavailable" not in proc.stderr
