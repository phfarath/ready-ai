"""
Tests for PageDomain P1-2 sanitization integration.

Quick win from the second commit in the P1-2 series: the
DOM HTML returned to the LLM must be sanitized (sensitive
values redacted, long non-sensitive values truncated).
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cdp.connection import CDPConnection
from src.cdp.page import ENV_DOM_MAX_CHARS, PageDomain
from src.cdp.sanitize import ENV_DOM_VALUE_MAX, ENV_RAW_DOM, REDACTED_SENTINEL


def _setup_page(html: str) -> tuple[PageDomain, AsyncMock]:
    conn = CDPConnection()
    conn._ws = AsyncMock()

    def fake_send(method, params=None, session_id=None, timeout=30.0):
        if method == "DOM.getDocument":
            return {"root": {"nodeId": 1}}
        if method == "DOM.getOuterHTML":
            return {"outerHTML": html}
        return {}

    conn.send = AsyncMock(side_effect=fake_send)
    return PageDomain(conn), conn


@pytest.mark.asyncio
async def test_password_value_is_redacted_by_default(monkeypatch):
    monkeypatch.delenv(ENV_RAW_DOM, raising=False)
    html = '<html><body><input type="password" value="hunter2"></body></html>'
    page, _ = _setup_page(html)
    out = await page.get_dom_html()
    assert "hunter2" not in out
    assert "[REDACTED]" in out


@pytest.mark.asyncio
async def test_long_non_sensitive_value_is_truncated(monkeypatch):
    monkeypatch.delenv(ENV_RAW_DOM, raising=False)
    monkeypatch.setenv(ENV_DOM_VALUE_MAX, "20")
    long = "x" * 200
    html = f'<html><body><input name="notes" value="{long}"></body></html>'
    page, _ = _setup_page(html)
    out = await page.get_dom_html()
    assert "x" * 200 not in out
    # The truncated form has the prefix + the cap + the marker.
    assert "x" * 20 in out


@pytest.mark.asyncio
async def test_raw_mode_redacts_sensitive_values(monkeypatch):
    monkeypatch.setenv(ENV_RAW_DOM, "true")
    html = '<html><body><input type="password" value="hunter2"></body></html>'
    page, _ = _setup_page(html)
    out = await page.get_dom_html()
    # In raw mode, sensitive values are still redacted.
    assert "hunter2" not in out
    assert REDACTED_SENTINEL in out


@pytest.mark.asyncio
async def test_script_tags_stripped(monkeypatch):
    monkeypatch.delenv(ENV_RAW_DOM, raising=False)
    html = "<html><body><script>alert('xss')</script>visible</body></html>"
    page, _ = _setup_page(html)
    out = await page.get_dom_html()
    assert "alert" not in out
    assert "visible" in out


@pytest.mark.asyncio
async def test_data_attrs_stripped_but_testid_kept(monkeypatch):
    monkeypatch.delenv(ENV_RAW_DOM, raising=False)
    html = (
        '<html><body><button data-pii="secret" data-testid="ok">click</button>'
        "</body></html>"
    )
    page, _ = _setup_page(html)
    out = await page.get_dom_html()
    assert "data-pii" not in out
    assert "secret" not in out
    assert 'data-testid="ok"' in out


@pytest.mark.asyncio
async def test_credit_card_via_autocomplete_is_redacted(monkeypatch):
    monkeypatch.delenv(ENV_RAW_DOM, raising=False)
    html = (
        '<html><body><input name="card" autocomplete="cc-number" '
        'value="4111111111111111"></body></html>'
    )
    page, _ = _setup_page(html)
    out = await page.get_dom_html()
    assert "4111111111111111" not in out
    assert "[REDACTED]" in out


@pytest.mark.asyncio
async def test_env_value_max_honoured(monkeypatch):
    monkeypatch.delenv(ENV_RAW_DOM, raising=False)
    monkeypatch.setenv(ENV_DOM_VALUE_MAX, "10")
    long = "y" * 50
    html = f'<html><body><input name="ok" value="{long}"></body></html>'
    page, _ = _setup_page(html)
    out = await page.get_dom_html()
    assert "y" * 10 in out
    # The original 50-char string is gone.
    assert "y" * 50 not in out


@pytest.mark.asyncio
async def test_truncation_cap_still_applied(monkeypatch):
    """The cap is applied AFTER sanitization, so a script-heavy
    page with the cap set should still respect the cap."""
    monkeypatch.delenv(ENV_RAW_DOM, raising=False)
    monkeypatch.setenv(ENV_DOM_MAX_CHARS, "50")
    long = "a" * 500
    html = f"<html><body>{long}</body></html>"
    page, _ = _setup_page(html)
    out = await page.get_dom_html()
    assert out.endswith("<!-- ... truncated ... -->")
