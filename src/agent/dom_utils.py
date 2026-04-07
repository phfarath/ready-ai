"""
Shared DOM utilities for the agent modules.

Contains the DOM fingerprinting logic used by both the AgenticLoop and
the DocTestRunner for detecting page state changes.
"""

import hashlib
import logging
import uuid

from ..cdp.runtime import RuntimeDomain

logger = logging.getLogger(__name__)


_DOM_FINGERPRINT_JS = """
(() => {
    const selectors = [
        'button', 'input', 'select', 'textarea',
        '[role="tab"]', '[role="menuitem"]',
        '[aria-expanded]', '[aria-selected]', '[data-state]'
    ].join(',');

    const normalize = (value) => (value || '')
        .replace(/\\s+/g, ' ')
        .trim()
        .slice(0, 50);

    const entries = Array.from(document.querySelectorAll(selectors))
        .filter(el => {
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        })
        .map(el => {
            const text = normalize(el.innerText || el.textContent || el.value);
            const state = [
                el.tagName.toLowerCase(),
                el.getAttribute('role') || '',
                text,
                el.getAttribute('aria-expanded') || '',
                el.getAttribute('aria-selected') || '',
                el.getAttribute('data-state') || '',
                el.hasAttribute('disabled') ? 'disabled' : 'enabled',
            ];
            return state.join('|');
        });

    const uniqueSorted = Array.from(new Set(entries)).sort();
    return uniqueSorted.slice(0, 250).join('\\n');
})()
"""


async def dom_fingerprint(runtime: RuntimeDomain) -> str:
    """
    Compute a fast MD5 hash of SPA-relevant interactive DOM state.

    On evaluation failure, returns a unique sentinel prefixed with
    `__fp_error__:` so two consecutive failed captures never compare equal
    — this prevents silent "DOM unchanged" verdicts from masking CDP or
    runtime flakiness. Callers comparing fingerprints will therefore see a
    mismatch on failure and can decide to retry.
    """
    try:
        payload = await runtime.evaluate(_DOM_FINGERPRINT_JS)
        payload_str = str(payload) if payload is not None else ""
    except Exception as exc:
        logger.debug(f"dom_fingerprint evaluation failed: {exc}")
        return f"__fp_error__:{uuid.uuid4().hex}"
    return hashlib.md5(payload_str.encode("utf-8")).hexdigest()
