"""Regression tests for VAL-QUAL-003: ``random`` imported at top-level
in ``connection.py``.

Previously ``import random`` lived inside the ``_reconnect`` method body.
This is bad practice: it is re-executed on every invocation and clutters
the hot path with an import system lookup.  The standard convention is to
place it with the other top-level imports.

These tests perform:

1. **Static inspection** (mirrors the ``rg -n "import random"`` evidence
   required by the validation contract) using AST parsing to distinguish
   top-level imports from nested (in-method) imports.
2. **Namespace check** confirming the ``random`` module is bound as a
   module-level attribute of ``src.cdp.connection``.
3. **Behavioural check** that ``_reconnect`` still computes a jittered
   backoff delay (the consumer of ``random.uniform``) so the reconnect
   loop remains functional.
"""

import ast
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

CONN_FILE = Path(__file__).parent.parent / "src" / "cdp" / "connection.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _module_body_imports(tree: ast.Module) -> set[str]:
    """Return the set of module names imported at the module top level."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
    return names


def _nested_random_imports(tree: ast.Module) -> list[int]:
    """Return line numbers of any ``import random`` nested in a function."""
    nested: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                if isinstance(child, ast.Import):
                    for alias in child.names:
                        if alias.name == "random":
                            nested.append(child.lineno)
    return nested


# ---------------------------------------------------------------------------
# Static inspection
# ---------------------------------------------------------------------------

def test_random_imported_at_top_level():
    """``connection.py`` MUST have a top-level ``import random`` statement."""
    src = CONN_FILE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    assert "random" in _module_body_imports(tree), (
        "connection.py must have a top-level 'import random' statement"
    )


def test_no_random_import_inside_methods():
    """No ``import random`` may appear inside any function/method body."""
    src = CONN_FILE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders = _nested_random_imports(tree)
    assert not offenders, (
        f"'import random' found inside method body at line(s) {offenders}; "
        f"move it to the module top-level imports."
    )


# ---------------------------------------------------------------------------
# Namespace check
# ---------------------------------------------------------------------------

def test_random_is_module_attribute():
    """The ``random`` module must be bound as a top-level attribute."""
    import src.cdp.connection as conn_mod

    assert hasattr(conn_mod, "random"), (
        "src.cdp.connection must expose 'random' as a module-level attribute"
    )


# ---------------------------------------------------------------------------
# Behavioural check: _reconnect still uses random for jitter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconnect_still_uses_random_for_jitter():
    """``_reconnect`` must still compute a jittered backoff delay.

    After moving ``import random`` to the top level, the ``_reconnect``
    method must still be able to call ``random.uniform`` without a
    ``NameError``.  We patch ``websockets.connect`` to always fail and
    ``asyncio.sleep`` to capture the delays.  The reconnect loop running
    to exhaustion and producing positive delays proves ``random`` is
    reachable.

    We do NOT assert specific delay values or attempt counts because
    other tests in the full suite monkeypatch ``RECONNECT_BASE_S`` and
    ``RECONNECT_MAX_ATTEMPTS`` at the ``connection_state`` module level;
    the jitter mechanics themselves are verified in
    ``test_cdp_reconnect_loop.py``.
    """
    from src.observability import init_run_context

    init_run_context("test-random-top-level-jitter")

    from src.cdp.connection import CDPConnection
    from src.cdp.connection_state import ConnectionState

    async def always_fail(*args, **kwargs):
        raise RuntimeError("network down")

    with patch("src.cdp.connection.websockets.connect", new=always_fail), \
         patch("src.cdp.connection.asyncio.sleep", new=AsyncMock()) as sleep_mock:
        conn = CDPConnection()
        conn._ws_url = "ws://test"
        conn._state = ConnectionState.DEGRADED
        await conn._reconnect()

        delays = [c.args[0] for c in sleep_mock.await_args_list]
        # The reconnect loop must have produced at least one delay,
        # proving random.uniform() was called without NameError.
        assert delays, "reconnect loop did not produce any backoff delays"
        # Every delay must be a positive number (base backoff + jitter).
        assert all(d > 0 for d in delays), (
            f"all delays must be positive, got {delays}"
        )
        # The circuit must have opened after exhaustion.
        assert conn._state == ConnectionState.DOWN
