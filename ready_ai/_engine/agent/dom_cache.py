"""DOM cache por step (Fase B Task 3 — P1/P2).

Consolida as 3 leituras CDP por contexto (dom_html + interactive
elements + url/fingerprint/title) numa chave com TTL curto. Hit evita
2-3 round-trips quando a página não mudou entre retries do mesmo step.
Ações mutantes invalidam explicitamente via `invalidate()`.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

from .dom_utils import FP_ERROR_PREFIX

_FINGERPRINT_JS = "(() => document.documentElement ? document.documentElement.outerHTML.length + '|' + document.title : '')()"

MUTATING_ACTIONS: frozenset[str] = frozenset({
    "click", "click_text", "type", "press_key", "navigate", "scroll",
    "scroll_to", "upload", "download", "dialog", "switch_tab", "close_tab",
})


@dataclass(frozen=True)
class DOMState:
    dom_html: str
    elements: str
    url: str
    fingerprint: str


class DOMCache:
    """LRU + TTL cache de estado DOM por (url, fingerprint, title)."""

    def __init__(self, ttl_s: float = 5.0, maxsize: int = 10) -> None:
        self.ttl_s = ttl_s
        self.maxsize = maxsize
        self._store: OrderedDict[str, tuple[DOMState, float]] = OrderedDict()

    def _remember(self, key: str, state: DOMState) -> None:
        self._store[key] = (state, time.monotonic())
        self._store.move_to_end(key)
        while len(self._store) > self.maxsize:
            self._store.popitem(last=False)

    def invalidate(self, action_type: Optional[str] = None) -> None:
        """Drop cached entries after a mutating action (or all when None)."""
        if action_type is None or action_type in MUTATING_ACTIONS:
            self._store.clear()

    def clear(self) -> None:
        self._store.clear()

    async def get(self, page, runtime, max_length: int = 4000) -> DOMState:
        """Return cached DOM state, fetching once per key inside the TTL."""
        url = await runtime.evaluate("window.location.href")
        title = await runtime.evaluate("document.title")
        try:
            fingerprint = await runtime.evaluate(_FINGERPRINT_JS)
            fingerprint = str(fingerprint)
        except Exception:
            fingerprint = f"{FP_ERROR_PREFIX}eval-failed"
        key = f"{url}\n{title}\n{fingerprint}"
        now = time.monotonic()
        hit = self._store.get(key)
        if hit is not None:
            state, ts = hit
            if now - ts <= self.ttl_s:
                self._store.move_to_end(key)
                return state
            del self._store[key]
        dom_html = await page.get_dom_html(max_length=max_length)
        elements = await runtime.get_interactive_elements()
        state = DOMState(dom_html=dom_html, elements=elements, url=url, fingerprint=fingerprint)
        self._remember(key, state)
        return state
