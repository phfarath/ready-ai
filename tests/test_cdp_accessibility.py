"""
Unit tests for the Accessibility domain (AX tree snapshot).

These tests use mocked CDP responses — no real Chrome required.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cdp.accessibility import (
    AccessibilityDomain,
    _format_node,
    get_ax_snapshot,
)


def _ax_node(role: str, *, name: str = "", value: str = "", description: str = "",
             disabled: bool = False, expanded: bool = False, required: bool = False,
             autocomplete: str = "", checked: bool | None = None) -> dict:
    """Build a minimal CDP Accessibility.getFullAXTree node payload."""
    properties: dict = {"role": {"value": role}}
    if name:
        properties["name"] = {"value": name}
    if value:
        properties["value"] = {"value": value}
    if description:
        properties["description"] = {"value": description}
    if disabled:
        properties["disabled"] = {"value": True}
    if expanded:
        properties["expanded"] = {"value": True}
    if required:
        properties["required"] = {"value": True}
    if autocomplete:
        properties["autocomplete"] = {"value": autocomplete}
    if checked is not None:
        properties["checked"] = {"value": checked}
    return {
        "nodeId": "1",
        "ignored": False,
        "role": {"value": role},
        "properties": properties,
    }


class TestFormatNode:
    def test_button_with_name(self):
        line = _format_node(_ax_node("button", name="Save"), raw=False)
        assert line == 'button "Save"'

    def test_textbox_with_state_and_value_redacted_by_default(self):
        # Default mode keeps the value but truncates if > 100 chars.
        # PII redaction only kicks in for *sensitive* fields.
        line = _format_node(
            _ax_node("textbox", name="Email", value="user@example.com"),
            raw=False,
        )
        assert "textbox" in line
        assert '"Email"' in line
        assert "value=user@example.com" in line

    def test_textbox_in_raw_mode_shows_value(self, monkeypatch):
        monkeypatch.setenv("READY_AI_RAW_DOM", "true")
        # Mirror production: format_node is called with raw=True when caller
        # has already consulted _is_raw_mode(). We assert raw path keeps the
        # value while still rendering the role+name.
        line_raw = _format_node(
            _ax_node("textbox", name="Email", value="user@example.com"),
            raw=True,
        )
        assert "user@example.com" in line_raw
        assert "Email" in line_raw

    def test_password_field_always_redacted(self):
        # Even in raw mode, autocomplete=current-password triggers redaction.
        line = _format_node(
            _ax_node("textbox", name="Password", value="hunter2",
                     autocomplete="current-password"),
            raw=True,
        )
        assert "hunter2" not in line
        assert "[REDACTED]" in line

    def test_sensitive_name_keyword_always_redacted(self):
        # "senha" is in our sensitive-keyword list (PT-BR friendly).
        line = _format_node(
            _ax_node("textbox", name="Senha", value="abc"),
            raw=True,
        )
        assert "abc" not in line
        assert "[REDACTED]" in line

    def test_long_value_truncated_when_not_sensitive(self):
        long_value = "x" * 250
        # raw=False -> truncate values longer than 100 chars to bound prompt.
        line = _format_node(
            _ax_node("textbox", name="Comments", value=long_value),
            raw=False,
        )
        assert "..." in line
        assert long_value not in line
        # Sanity: starts with role + name and the value field has 100 x's.
        assert line.startswith('textbox "Comments" value=')
        assert "xxxxxxxxxx" in line  # at least 10 of the x's present
        # And the raw path preserves the value untouched.
        line_raw = _format_node(
            _ax_node("textbox", name="Comments", value=long_value),
            raw=True,
        )
        assert long_value in line_raw

    def test_state_flags_rendered(self):
        line = _format_node(
            _ax_node("button", name="Submit", disabled=True, expanded=False),
            raw=False,
        )
        assert "disabled" in line

    def test_skips_uninteresting_roles(self):
        assert _format_node(_ax_node("generic"), raw=False) is None
        assert _format_node(_ax_node("presentation"), raw=False) is None

    def test_structural_roles_kept_even_without_name(self):
        # navigation is structural and should appear with role alone.
        line = _format_node(_ax_node("navigation"), raw=False)
        assert line == "navigation"


class TestGetAxSnapshot:
    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        conn = AsyncMock()
        conn.send = AsyncMock(side_effect=RuntimeError("ws closed"))
        out = await get_ax_snapshot(conn, max_nodes=10)
        assert out == ""

    @pytest.mark.asyncio
    async def test_returns_compact_lines(self):
        conn = AsyncMock()
        conn.send = AsyncMock(
            return_value={
                "nodes": [
                    _ax_node("button", name="Save"),
                    _ax_node("textbox", name="Email", value="user@example.com"),
                    _ax_node("generic"),  # filtered out
                    _ax_node("link", name="Docs"),
                ]
            }
        )
        out = await get_ax_snapshot(conn, max_nodes=10)
        assert 'button "Save"' in out
        assert 'link "Docs"' in out
        assert "generic" not in out
        # Non-sensitive values are kept (helps the planner; LGPD-relevant
        # fields are redacted by _format_node based on autocomplete/name).
        assert "user@example.com" in out

    @pytest.mark.asyncio
    async def test_respects_max_nodes(self):
        conn = AsyncMock()
        conn.send = AsyncMock(
            return_value={
                "nodes": [_ax_node("button", name=f"B{i}") for i in range(500)]
            }
        )
        out = await get_ax_snapshot(conn, max_nodes=5)
        # 5 real lines + 1 truncation notice line.
        assert out.count("\n") + 1 == 6
        assert "truncated" in out


class TestAccessibilityDomain:
    @pytest.mark.asyncio
    async def test_enable_swallows_errors(self):
        conn = AsyncMock()
        conn.send = AsyncMock(side_effect=RuntimeError("not connected"))
        dom = AccessibilityDomain(conn)
        # Should not raise.
        await dom.enable()

    @pytest.mark.asyncio
    async def test_get_snapshot_delegates(self):
        conn = AsyncMock()
        conn.send = AsyncMock(return_value={"nodes": [_ax_node("button", name="Go")]})
        dom = AccessibilityDomain(conn)
        out = await dom.get_snapshot(max_nodes=5)
        assert 'button "Go"' in out
