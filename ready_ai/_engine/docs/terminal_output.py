"""
Terminal Output — colored progress and summary for doc test runs.

Respects NO_COLOR env var and non-TTY output (pipes/redirects).
"""

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent.test_runner import DocTestReport, StepTestResult


def _supports_color() -> bool:
    """Check if the terminal supports ANSI color codes."""
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty"):
        return False
    return sys.stdout.isatty()


class _Colors:
    """ANSI color codes, disabled when NO_COLOR is set or output is piped."""

    def __init__(self, enabled: bool):
        self._e = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self._e else text

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def blue(self, text: str) -> str:
        return self._wrap("34", text)

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)


_STATUS_FN = {
    "PASSED": lambda c, t: c.green(t),
    "DRIFT": lambda c, t: c.yellow(t),
    "BROKEN": lambda c, t: c.red(t),
    "HEALED": lambda c, t: c.blue(t),
}


class ProgressPrinter:
    """Prints step-by-step progress and a final summary table."""

    def __init__(self):
        self._c = _Colors(_supports_color())

    def header(self, doc_path: str, url: str, threshold: float) -> None:
        """Print test run header."""
        print(f"\n{self._c.bold('Doc Test Runner')}")
        print(f"  Document:  {doc_path}")
        print(f"  URL:       {url}")
        print(f"  Threshold: {threshold:.0%}")
        print()

    def step_start(self, step_num: int, total: int, title: str) -> None:
        """Print step start indicator."""
        progress = self._c.dim(f"[{step_num}/{total}]")
        print(f"  {progress} Testing: {title}", end="", flush=True)

    def step_result(self, result: "StepTestResult") -> None:
        """Print step result on the same line."""
        status_fn = _STATUS_FN.get(result.status, lambda c, t: t)
        status_str = status_fn(self._c, result.status)
        sim = f"{result.visual_similarity:.0%}"
        print(f" — {status_str} ({sim})")

    def summary(self, report: "DocTestReport") -> None:
        """Print final summary table."""
        total = len(report.results)
        passed = sum(1 for r in report.results if r.status == "PASSED")
        drift = sum(1 for r in report.results if r.status == "DRIFT")
        broken = sum(1 for r in report.results if r.status == "BROKEN")
        healed = sum(1 for r in report.results if r.status == "HEALED")

        print()
        # Summary table
        header = f"  {'#':<4} {'Step':<40} {'Status':<10} {'Similarity':<12} {'DOM'}"
        print(self._c.bold(header))
        print(f"  {'—' * 78}")

        for r in report.results:
            status_fn = _STATUS_FN.get(r.status, lambda c, t: t)
            status_str = status_fn(self._c, f"{r.status:<10}")
            dom = "changed" if r.dom_changed else "—"
            title = r.title[:38] + ".." if len(r.title) > 40 else r.title
            print(f"  {r.step_number:<4} {title:<40} {status_str} {r.visual_similarity:<12.1%} {dom}")

        print(f"  {'—' * 78}")

        # Overall result
        status_fn = _STATUS_FN.get(report.overall_status, lambda c, t: t)
        overall = status_fn(self._c, report.overall_status)
        parts = [f"{self._c.green(str(passed))} passed"]
        if drift:
            parts.append(f"{self._c.yellow(str(drift))} drift")
        if broken:
            parts.append(f"{self._c.red(str(broken))} broken")
        if healed:
            parts.append(f"{self._c.blue(str(healed))} healed")

        print(f"\n  Result: {overall}  ({', '.join(parts)} of {total})")
        print()
