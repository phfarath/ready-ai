"""CLI stdio hardening (READY-AI-T-CLI-STDIO): output must survive any parent codepage.

Dogfood 2026-10-21: capturing the CLI with ``subprocess.run(text=True)`` on
Windows broke with ``UnicodeDecodeError: 'charmap' codec can't decode byte
0x8f`` because log lines carry emoji and the parent decodes cp1252.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import main as cli_main

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_configure_stdio_escapes_non_ascii_to_pure_ascii(monkeypatch):
    """A piped (non-tty) cp1252 stream emits ASCII-only bytes after setup."""
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="cp1252")
    assert not stream.isatty()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    cli_main._configure_stdio()
    print("🏃 ready-ai — Declarative Run-Flow ❌")
    stream.flush()
    raw = buf.getvalue()
    assert raw.decode("ascii")  # strict: must not raise
    assert raw.decode("cp1252")  # strict: parent codepage must not raise either
    assert b"ready-ai" in raw  # content preserved, only glyphs escaped


def test_configure_stdio_tty_keeps_encoding_never_crashes(monkeypatch):
    """An interactive cp1252 console degrades emoji to '?' instead of raising."""

    class TtyStream(io.TextIOWrapper):
        def isatty(self):
            return True

    buf = io.BytesIO()
    stream = TtyStream(buf, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    cli_main._configure_stdio()
    print("🏃 ready-ai ❌")  # must not raise UnicodeEncodeError
    stream.flush()
    assert buf.getvalue().decode("cp1252")


def test_cli_error_path_decodable_under_cp1252_and_utf8():
    """Regression: run-flow banner (emoji) survives a cp1252 parent.

    Forces the child to emit UTF-8 (like PYTHONUTF8=1 did during dogfood)
    and decodes both streams strictly with cp1252 and utf-8. Pre-fix the
    cp1252 decode raises UnicodeDecodeError on the emoji bytes.
    """
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "main.py", "run-flow", "--config", "nonexistent-dogfood.yaml"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=False,
        timeout=120,
    )
    assert proc.returncode != 0  # missing config must still fail loudly
    # Strict decodes: any undecodable byte raises (that is the assertion).
    proc.stderr.decode("cp1252")
    proc.stderr.decode("utf-8")
    proc.stdout.decode("cp1252")
    proc.stdout.decode("utf-8")
