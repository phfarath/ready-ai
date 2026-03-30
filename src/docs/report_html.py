"""
HTML Report Renderer — generates a standalone HTML report from DocTestReport.

The report is self-contained (inline CSS, base64-embedded images) and can be
opened in any browser without external dependencies.
"""

import base64
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent.test_runner import DocTestReport

logger = logging.getLogger(__name__)

_STATUS_COLORS = {
    "PASSED": "#22c55e",
    "DRIFT": "#eab308",
    "BROKEN": "#ef4444",
    "DRIFT_DETECTED": "#eab308",
    "HEALED": "#3b82f6",
}

_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #0f172a; color: #e2e8f0; padding: 2rem; line-height: 1.6; }
.container { max-width: 1200px; margin: 0 auto; }
h1 { font-size: 1.75rem; margin-bottom: 0.5rem; }
.meta { color: #94a3b8; font-size: 0.875rem; margin-bottom: 1.5rem; }
.badge { display: inline-block; padding: 0.25rem 0.75rem; border-radius: 9999px;
         font-weight: 600; font-size: 0.875rem; text-transform: uppercase; }
.summary-bar { display: flex; gap: 1.5rem; margin-bottom: 2rem; padding: 1rem;
               background: #1e293b; border-radius: 0.5rem; }
.summary-item { text-align: center; }
.summary-item .count { font-size: 2rem; font-weight: 700; }
.summary-item .label { font-size: 0.75rem; color: #94a3b8; text-transform: uppercase; }
.step-card { background: #1e293b; border-radius: 0.5rem; margin-bottom: 1rem;
             overflow: hidden; border-left: 4px solid; }
.step-header { display: flex; justify-content: space-between; align-items: center;
               padding: 1rem 1.25rem; cursor: pointer; }
.step-header:hover { background: #334155; }
.step-title { font-weight: 500; }
.step-details { padding: 0 1.25rem 1.25rem; display: none; }
.step-details.open { display: block; }
.step-meta { display: flex; gap: 1rem; font-size: 0.8rem; color: #94a3b8; margin-bottom: 0.75rem; }
.diff-container { display: flex; gap: 4px; overflow-x: auto; margin-top: 0.75rem; }
.diff-container img { max-height: 300px; border-radius: 0.25rem; }
.diff-label { font-size: 0.7rem; color: #94a3b8; text-align: center; margin-top: 0.25rem; }
.error-msg { background: #7f1d1d; color: #fca5a5; padding: 0.75rem; border-radius: 0.25rem;
             font-family: monospace; font-size: 0.85rem; margin-top: 0.5rem; }
.semantic-desc { background: #1e3a5f; color: #93c5fd; padding: 0.75rem; border-radius: 0.25rem;
                 font-size: 0.85rem; margin-top: 0.5rem; font-style: italic; }
table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
th, td { padding: 0.5rem 0.75rem; text-align: left; border-bottom: 1px solid #334155; }
th { color: #94a3b8; font-size: 0.75rem; text-transform: uppercase; }
"""

_JS = """
document.querySelectorAll('.step-header').forEach(h => {
    h.addEventListener('click', () => {
        h.nextElementSibling.classList.toggle('open');
    });
});
"""


def _img_to_data_uri(path: str | Path) -> str:
    """Convert an image file to a data URI for inline embedding."""
    p = Path(path)
    if not p.exists():
        return ""
    data = base64.b64encode(p.read_bytes()).decode()
    return f"data:image/png;base64,{data}"


def render_html_report(report: "DocTestReport", output_path: str | Path) -> str:
    """
    Render a DocTestReport as a standalone HTML file.

    Args:
        report: The test report to render.
        output_path: Path to write the HTML file.

    Returns:
        The path to the generated HTML file.
    """
    total = len(report.results)
    passed = sum(1 for r in report.results if r.status == "PASSED")
    drift = sum(1 for r in report.results if r.status == "DRIFT")
    broken = sum(1 for r in report.results if r.status == "BROKEN")
    healed = sum(1 for r in report.results if r.status == "HEALED")

    status_color = _STATUS_COLORS.get(report.overall_status, "#94a3b8")

    # Build step cards
    step_cards = []
    for r in report.results:
        color = _STATUS_COLORS.get(r.status, "#94a3b8")
        card = f"""
        <div class="step-card" style="border-left-color: {color};">
            <div class="step-header">
                <span class="step-title">Step {r.step_number}: {_esc(r.title)}</span>
                <span class="badge" style="background: {color}; color: #0f172a;">{r.status}</span>
            </div>
            <div class="step-details">
                <div class="step-meta">
                    <span>Similarity: <strong>{r.visual_similarity:.1%}</strong></span>
                    <span>DOM changed: <strong>{'Yes' if r.dom_changed else 'No'}</strong></span>
                </div>"""

        # Semantic description (Phase 3.3)
        semantic = getattr(r, "semantic_description", "")
        if semantic:
            card += f'\n<div class="semantic-desc">{_esc(semantic)}</div>'

        # Error message for broken steps
        if r.error:
            card += f'\n<div class="error-msg">{_esc(r.error)}</div>'

        # Diff images for drift steps
        if r.status == "DRIFT" and r.diff_image_path:
            diff_uri = _img_to_data_uri(r.diff_image_path)
            if diff_uri:
                card += f"""
                <div class="diff-container">
                    <div>
                        <img src="{diff_uri}" alt="Visual diff for step {r.step_number}">
                        <div class="diff-label">Baseline | Current | Diff</div>
                    </div>
                </div>"""

        # Current screenshot
        if r.new_screenshot_path:
            screenshot_uri = _img_to_data_uri(r.new_screenshot_path)
            if screenshot_uri:
                card += f"""
                <div style="margin-top: 0.75rem;">
                    <img src="{screenshot_uri}" alt="Current screenshot" style="max-height: 250px; border-radius: 0.25rem;">
                    <div class="diff-label">Current screenshot</div>
                </div>"""

        card += "\n</div>\n</div>"
        step_cards.append(card)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Doc Test Report — {_esc(report.overall_status)}</title>
    <style>{_CSS}</style>
</head>
<body>
    <div class="container">
        <h1>Documentation Test Report
            <span class="badge" style="background: {status_color}; color: #0f172a; margin-left: 0.5rem;">
                {report.overall_status}
            </span>
        </h1>
        <div class="meta">
            <div>Document: {_esc(report.doc_path)}</div>
            <div>URL: {_esc(report.url)} &middot; Threshold: {report.threshold:.0%} &middot; {_esc(report.timestamp)}</div>
        </div>

        <div class="summary-bar">
            <div class="summary-item">
                <div class="count">{total}</div>
                <div class="label">Total</div>
            </div>
            <div class="summary-item">
                <div class="count" style="color: #22c55e;">{passed}</div>
                <div class="label">Passed</div>
            </div>
            <div class="summary-item">
                <div class="count" style="color: #eab308;">{drift}</div>
                <div class="label">Drift</div>
            </div>
            <div class="summary-item">
                <div class="count" style="color: #ef4444;">{broken}</div>
                <div class="label">Broken</div>
            </div>
            {"" if not healed else f'<div class="summary-item"><div class="count" style="color: #3b82f6;">{healed}</div><div class="label">Healed</div></div>'}
        </div>

        {"".join(step_cards)}

        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Step</th>
                    <th>Status</th>
                    <th>Similarity</th>
                    <th>DOM Changed</th>
                </tr>
            </thead>
            <tbody>
                {"".join(
                    f'<tr><td>{r.step_number}</td><td>{_esc(r.title)}</td>'
                    f'<td><span class="badge" style="background: {_STATUS_COLORS.get(r.status, "#94a3b8")}; color: #0f172a; font-size: 0.75rem;">{r.status}</span></td>'
                    f'<td>{r.visual_similarity:.1%}</td>'
                    f'<td>{"Yes" if r.dom_changed else "No"}</td></tr>'
                    for r in report.results
                )}
            </tbody>
        </table>
    </div>
    <script>{_JS}</script>
</body>
</html>"""

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    logger.info(f"HTML report saved: {out}")
    return str(out)


def _esc(text: str) -> str:
    """Escape HTML special characters using stdlib html.escape."""
    from html import escape
    return escape(text, quote=True)
