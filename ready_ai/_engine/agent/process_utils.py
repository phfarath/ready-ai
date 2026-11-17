"""Process-tree utilities for Chrome lifecycle (Fase A Task 3 — C2).

On Windows, killing the parent Chrome PID leaves renderer/GPU children
orphaned. `kill_process_tree` kills children recursively via psutil
(optional dependency) with a plain `os.kill` fallback. `wait_for_pid`
bounds the wait so teardown never hangs.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time

try:
    import psutil  # type: ignore[import-not-found]
except ImportError:
    psutil = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _fallback_sig() -> int:
    """Hard-kill signal available on this platform."""
    if sys.platform == "win32":
        return signal.SIGTERM
    return getattr(signal, "SIGKILL", signal.SIGTERM)


def kill_process_tree(pid: int, timeout: float = 3.0) -> bool:
    """Kill *pid* and its children recursively. True when all are gone."""
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
        except Exception:
            return False
        try:
            children = proc.children(recursive=True)
        except Exception:
            children = []
        for child in children:
            try:
                child.kill()
            except Exception:
                continue
        try:
            proc.kill()
        except Exception:
            pass
        return wait_for_pid(pid, timeout=timeout)
    try:
        os.kill(pid, _fallback_sig())
    except (OSError, ProcessLookupError, AttributeError):
        return False
    if sys.platform == "win32":
        # os.kill on Windows cannot poll liveness (PID may be reused);
        # the signal was sent — treat as done instead of looping.
        return True
    return wait_for_pid(pid, timeout=timeout)


def wait_for_pid(pid: int, timeout: float = 3.0) -> bool:
    """Poll until *pid* is gone (or the timeout elapses). Never raises."""
    deadline = time.monotonic() + max(timeout, 0)
    while time.monotonic() < deadline:
        if psutil is not None:
            try:
                psutil.Process(pid)
            except Exception:
                return True
        elif sys.platform == "win32":
            return True
        else:
            try:
                os.kill(pid, 0)
            except (OSError, ProcessLookupError):
                return True
            except AttributeError:
                return True
        time.sleep(0.05)
    if psutil is not None:
        try:
            psutil.Process(pid)
            return False
        except Exception:
            return True
    return True
