"""TDD — R7: `click` sem checar quad com área zero / fora do viewport.

Hoje o click resolve o box model e dispara mousePressed/mouseReleased
no centro mesmo quando o elemento está oculto (quad degenerado) ou
fora da tela — o clique atinge o elemento errado. Fix: quad com área
zero retorna False sem disparar; fora do viewport faz scrollIntoView
e revalida antes de clicar.
"""

from unittest.mock import AsyncMock

import pytest

from src.cdp.input import InputDomain


def _conn_for(getbox_sequence, viewport=(1280, 800)):
    """Fake conn: getBoxModel yields each quad in sequence per call."""
    calls = []
    state = {"box_calls": 0}

    async def send(method, params=None, session_id=None):
        calls.append((method, params))
        if method == "DOM.getDocument":
            return {"root": {"nodeId": 1}}
        if method == "DOM.querySelector":
            return {"nodeId": 7}
        if method == "DOM.getBoxModel":
            idx = min(state["box_calls"], len(getbox_sequence) - 1)
            state["box_calls"] += 1
            return {"model": {"content": getbox_sequence[idx]}}
        if method == "Runtime.evaluate":
            expr = (params or {}).get("expression", "")
            if "innerWidth" in expr:
                return {"result": {"value": {"w": viewport[0], "h": viewport[1]}}}
            return {"result": {"value": None}}
        return {}

    conn = AsyncMock()
    conn.send = send
    return conn, calls


def _dispatched(calls, method):
    return [p for m, p in calls if m == method]


class TestR7ClickVisibility:
    @pytest.mark.asyncio
    async def test_zero_area_quad_returns_false_without_dispatch(self):
        conn, calls = _conn_for([[0, 0, 0, 0, 0, 0, 0, 0]])
        result = await InputDomain(conn).click("#hidden")
        assert result is False
        assert _dispatched(calls, "Input.dispatchMouseEvent") == []

    @pytest.mark.asyncio
    async def test_missing_box_model_returns_false(self):
        conn = AsyncMock()
        conn.send = AsyncMock(
            side_effect=[
                {"root": {"nodeId": 1}},
                {"nodeId": 7},
                {"model": {}},
            ]
        )
        result = await InputDomain(conn).click("#broken")
        assert result is False

    @pytest.mark.asyncio
    async def test_off_viewport_scrolls_into_view_then_clicks(self):
        below_fold = [100, 5000, 200, 5000, 200, 5100, 100, 5100]
        in_view = [100, 300, 200, 300, 200, 400, 100, 400]
        conn, calls = _conn_for([below_fold, in_view])
        result = await InputDomain(conn).click("#far")
        assert result is True
        scrolls = [
            p for m, p in calls
            if m == "Runtime.evaluate" and "scrollIntoView" in str((p or {}).get("expression", ""))
        ]
        assert len(scrolls) == 1
        presses = [
            p for p in _dispatched(calls, "Input.dispatchMouseEvent")
            if p.get("type") == "mousePressed"
        ]
        assert len(presses) == 1
        assert presses[0]["y"] == pytest.approx(350.0)

    @pytest.mark.asyncio
    async def test_visible_element_clicks_without_scroll(self):
        quad = [100, 300, 200, 300, 200, 400, 100, 400]
        conn, calls = _conn_for([quad])
        result = await InputDomain(conn).click("#btn")
        assert result is True
        scrolls = [
            p for m, p in calls
            if m == "Runtime.evaluate" and "scrollIntoView" in str((p or {}).get("expression", ""))
        ]
        assert scrolls == []

    @pytest.mark.asyncio
    async def test_still_off_viewport_after_scroll_returns_false(self):
        below_fold = [100, 5000, 200, 5000, 200, 5100, 100, 5100]
        conn, calls = _conn_for([below_fold, below_fold])
        result = await InputDomain(conn).click("#stuck")
        assert result is False
        assert _dispatched(calls, "Input.dispatchMouseEvent") == []
