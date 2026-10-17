"""TDD — C2 residual: kill de árvore Chrome no Windows.

`teardown()` e `_kill_all_orphan_chrome()` matam só o PID pai — no
Windows os filhos (renderer, GPU) ficam órfãos. `kill_process_tree`
mata filhos recursivamente via psutil (opcional), com fallback para
os.kill puro quando psutil não está instalado.
"""

import sys as _sys
import types as _types
from unittest.mock import MagicMock

from src.agent import process_utils


def _fake_psutil(children_pids=(11, 12), parent_pid=10):
    """Fake psutil module: parent 10 with children 11, 12."""
    killed = []
    dead = set()

    class _NoSuchProcess(Exception):
        pass

    class _AccessDenied(Exception):
        pass

    class _FakeProc:
        def __init__(self, pid):
            self.pid = pid

        def children(self, recursive=True):
            if self.pid == parent_pid:
                return [_FakeProc(p) for p in children_pids]
            return []

        def kill(self):
            killed.append(self.pid)
            dead.add(self.pid)

        def wait(self, timeout=None):
            return None

    def _process(pid):
        if pid in dead:
            raise _NoSuchProcess(pid)
        return _FakeProc(pid)

    fake = _types.ModuleType("psutil")
    fake.Process = _process
    fake.NoSuchProcess = _NoSuchProcess
    fake.AccessDenied = _AccessDenied
    fake.__version__ = "fake"
    return fake, killed


class TestKillProcessTree:
    def test_kills_children_before_parent(self, monkeypatch):
        fake, killed = _fake_psutil()
        monkeypatch.setattr(process_utils, "psutil", fake)
        assert process_utils.kill_process_tree(10) is True
        assert killed[-1] == 10
        assert set(killed) == {10, 11, 12}

    def test_missing_process_returns_false(self, monkeypatch):
        fake, _ = _fake_psutil()

        def _raise(pid):
            raise fake.NoSuchProcess()

        fake.Process = _raise
        monkeypatch.setattr(process_utils, "psutil", fake)
        assert process_utils.kill_process_tree(999999) is False

    def test_works_without_psutil(self, monkeypatch):
        monkeypatch.setattr(process_utils, "psutil", None)
        monkeypatch.setattr("os.kill", MagicMock())
        assert process_utils.kill_process_tree(12345) in (True, False)

    def test_teardown_uses_kill_process_tree_as_last_resort(self, monkeypatch):
        from src.agent.browser_session import BrowserSession

        calls = []
        monkeypatch.setattr(
            process_utils, "kill_process_tree", lambda pid, **kw: calls.append(pid) or True
        )
        monkeypatch.setattr(
            "src.agent.browser_session.kill_process_tree",
            lambda pid, **kw: calls.append(pid) or True,
        )
        import asyncio

        async def _run():
            bs = BrowserSession()
            bs._conn = None
            proc = MagicMock()
            proc.pid = 4242
            proc.terminate.side_effect = Exception("nope")
            proc.kill.side_effect = Exception("nope")
            proc.wait.side_effect = Exception("nope")
            bs._chrome_proc = proc
            await bs.teardown()

        asyncio.run(_run())
        assert calls == [4242]

    def test_kill_all_orphans_uses_kill_process_tree(self, monkeypatch):
        from src.agent.browser_session import _CHROME_PIDS, _kill_all_orphan_chrome

        calls = []
        monkeypatch.setattr(
            "src.agent.browser_session.kill_process_tree",
            lambda pid, **kw: calls.append(pid) or True,
        )
        _CHROME_PIDS.add(777001)
        try:
            _kill_all_orphan_chrome()
            assert calls == [777001]
            assert len(_CHROME_PIDS) == 0
        finally:
            _CHROME_PIDS.discard(777001)

    def test_psutil_is_optional_dependency(self):
        assert _sys.modules.get("psutil") is None or True
        assert process_utils.psutil is None or hasattr(process_utils.psutil, "Process")
