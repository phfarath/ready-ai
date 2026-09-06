"""
Browser Session — Chrome lifecycle, authentication, and crash recovery.

Encapsulates launching Chrome, CDP connection management, cookie injection,
login form handling, and browser crash recovery.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from ..cdp.browser import launch_chrome, get_ws_url
from ..cdp.connection import CDPConnection
from ..cdp.connection_state import ConnectionState
from ..cdp.page import PageDomain
from ..cdp.input import InputDomain
from ..cdp.runtime import RuntimeDomain
from ..observability import log_event

if TYPE_CHECKING:
    from ..llm.client import LLMClient

logger = logging.getLogger(__name__)

# ─── Global Chrome process registry ──────────────────────────────────────────
# Tracks live Chrome PIDs so we can force-kill orphans on unexpected shutdowns.

_CHROME_PIDS: set[int] = set()


def _register_chrome_pid(pid: int) -> None:
    _CHROME_PIDS.add(pid)


def _unregister_chrome_pid(pid: int) -> None:
    _CHROME_PIDS.discard(pid)


def _kill_all_orphan_chrome() -> None:
    """Emergency cleanup — called by atexit to prevent zombie Chrome."""
    for pid in list(_CHROME_PIDS):
        try:
            if sys.platform == "win32":
                # signal.SIGKILL does not exist on Windows; os.kill with
                # any non-console signal calls TerminateProcess (hard kill).
                os.kill(pid, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGKILL)  # force kill
            time.sleep(0.1)
        except (OSError, ProcessLookupError, AttributeError):
            pass
    _CHROME_PIDS.clear()


atexit.register(_kill_all_orphan_chrome)


def _normalize_cookie(cookie: dict) -> dict | None:
    """
    Normalize a single cookie dict from a browser-export JSON into the CDP
    Network.setCookie param shape. Returns None if the cookie lacks the
    required name/value fields.
    """
    if "name" not in cookie or "value" not in cookie:
        return None
    cdp_cookie = {
        "name": cookie["name"],
        "value": cookie["value"],
        "domain": cookie.get("domain", ""),
        "path": cookie.get("path", "/"),
        "secure": cookie.get("secure", False),
        "httpOnly": cookie.get("httpOnly", False),
    }
    if "sameSite" in cookie:
        cdp_cookie["sameSite"] = cookie["sameSite"]
    # Browser exports commonly use `expirationDate` — translate to CDP `expires`.
    if "expirationDate" in cookie:
        cdp_cookie["expires"] = cookie["expirationDate"]
    elif "expires" in cookie:
        cdp_cookie["expires"] = cookie["expires"]
    return cdp_cookie


async def inject_cookies_from_file(conn, cookies_file: str) -> int:
    """
    Read a cookies JSON file, normalize each entry, and send it via
    Network.setCookie on the given connection. Returns the count of cookies
    successfully injected. Raises FileNotFoundError / ValueError on IO or
    shape problems so callers can log uniformly.
    """
    cookie_path = Path(cookies_file)
    if not cookie_path.exists():
        raise FileNotFoundError(cookies_file)
    cookies = json.loads(cookie_path.read_text())
    if not isinstance(cookies, list):
        raise ValueError(
            "Cookies file must contain a JSON array of cookie objects"
        )
    count = 0
    for cookie in cookies:
        cdp_cookie = _normalize_cookie(cookie)
        if cdp_cookie is None:
            continue
        await conn.send("Network.setCookie", cdp_cookie)
        count += 1
    return count


class BrowserSession:
    """Owns the full Chrome browser lifecycle."""

    def __init__(
        self,
        port: int = 9222,
        headless: bool = False,
        cookies_file: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        profile_dir: Optional[str] = None,
    ) -> None:
        self.port = port
        self.headless = headless
        self.cookies_file = cookies_file
        self.username = username
        self.password = password
        # Explicit persistent Chrome profile (SSO logins survive across
        # runs). None = ephemeral temp profile owned by this session.
        self.profile_dir = profile_dir
        self._temp_profile_dir: Optional[str] = None

        self._chrome_proc = None
        self._conn: Optional[CDPConnection] = None
        self._page: Optional[PageDomain] = None
        self._input: Optional[InputDomain] = None
        self._runtime: Optional[RuntimeDomain] = None

    @property
    def conn(self) -> CDPConnection:
        return self._conn

    @property
    def page(self) -> PageDomain:
        return self._page

    @property
    def input_domain(self) -> InputDomain:
        return self._input

    @property
    def runtime(self) -> RuntimeDomain:
        return self._runtime

    @property
    def is_disconnected(self) -> bool:
        """True when the underlying CDP connection is in a terminal state.

        Combines two cases:
          * DOWN — the circuit breaker opened after CB_THRESHOLD
            consecutive reconnect failures. The orchestrator should
            call `recover(url)` to respawn Chrome.
          * CLOSED — `teardown()` was called explicitly. No recovery
            is appropriate.

        The agent loop should check this property before issuing
        commands when P0-1 reconnect is enabled, so it can avoid
        30s of waiting on a doomed `send` and instead trigger
        `recover` right away.
        """
        return self._conn is not None and self._conn.is_disconnected

    @property
    def is_reconnecting(self) -> bool:
        """True while the connection's auto-reconnect/reattach is in flight.

        READY-AI-T-3: the loop uses this to decide between "wait for
        the connection to heal itself" and "full respawn via
        `recover()`".
        """
        return self._conn is not None and self._conn.reconnecting

    async def wait_for_reconnect(
        self,
        timeout: float,
        poll_interval: float = 0.1,
    ) -> Optional[ConnectionState]:
        """Wait (bounded) for the connection's own reconnect+reattach
        to resolve the FSM. Returns the final connection state, or
        None when no connection exists."""
        if self._conn is None:
            return None
        return await self._conn.wait_for_reconnect(
            timeout=timeout, poll_interval=poll_interval
        )

    @property
    def cdp_state(self) -> Optional[str]:
        """Diagnostic: expose the underlying FSM state as a string."""
        if self._conn is None:
            return None
        return self._conn.state.value

    async def setup(self) -> None:
        """Launch Chrome and establish CDP connection."""
        logger.info("Launching Chrome...")
        try:
            # READY-AI-T-PH2D (M12): own the temp profile directory instead
            # of letting launch_chrome create an untracked one — teardown
            # removes exactly what we created, never an explicit dir.
            user_data_dir = self.profile_dir
            if user_data_dir is None:
                user_data_dir = tempfile.mkdtemp(prefix="ready-ai-chrome-")
                self._temp_profile_dir = user_data_dir
            self._chrome_proc = launch_chrome(
                port=self.port,
                headless=self.headless,
                user_data_dir=user_data_dir,
            )
            if self._chrome_proc and self._chrome_proc.pid:
                _register_chrome_pid(self._chrome_proc.pid)
            ws_url = await get_ws_url(port=self.port)
            self._conn = CDPConnection()
            await self._conn.connect(ws_url)
            await self._conn.attach_to_page()
            self._init_domains()
        except Exception:
            await self.teardown()
            raise

    def _init_domains(self) -> None:
        """Create CDP domain helpers from current connection."""
        self._page = PageDomain(self._conn)
        self._input = InputDomain(self._conn)
        self._runtime = RuntimeDomain(self._conn)

    async def teardown(self) -> None:
        """Close connections and kill Chrome process with orphan prevention."""
        if self._conn:
            # P0-1: surface the FSM state in the teardown log so we
            # can correlate "normal teardown" vs. "teardown after
            # circuit open" in the run summary.
            prior_state = self.cdp_state or "unknown"
            try:
                await self._conn.close()
            except Exception:
                pass
            log_event("browser_teardown", cdp_state=prior_state)

        if self._chrome_proc:
            try:
                # Graceful shutdown attempt
                self._chrome_proc.terminate()
                self._chrome_proc.wait(timeout=2)
            except Exception:
                try:
                    # Force kill if graceful fails
                    self._chrome_proc.kill()
                    self._chrome_proc.wait(timeout=2)
                except Exception:
                    # Last resort: os-level SIGKILL (POSIX only).
                    # On Windows signal.SIGKILL does not exist and
                    # proc.kill() above already calls TerminateProcess.
                    if sys.platform != "win32":
                        try:
                            os.kill(self._chrome_proc.pid, signal.SIGKILL)
                        except (OSError, ProcessLookupError):
                            pass
            finally:
                _unregister_chrome_pid(self._chrome_proc.pid)
                self._chrome_proc = None
                self._conn = None
            logger.info("Chrome process terminated")

        # READY-AI-T-PH2D (M12): temp profiles are always cleaned up —
        # including when setup failed before a process existed. An
        # explicit persistent dir is never touched.
        temp_dir, self._temp_profile_dir = self._temp_profile_dir, None
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.debug(f"Removed temp Chrome profile: {temp_dir}")

    async def inject_cookies(self) -> None:
        """Inject cookies from a JSON file for session authentication."""
        if not self.cookies_file:
            return
        try:
            count = await inject_cookies_from_file(self._conn, self.cookies_file)
            if count:
                logger.info(f"Injected {count} cookies from {self.cookies_file}")
        except FileNotFoundError:
            logger.error(f"Cookies file not found: {self.cookies_file}")
        except ValueError as e:
            logger.error(str(e))
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to parse cookies file: {e}")

    async def handle_login(self, llm: LLMClient) -> None:
        """
        Automatically detect and fill a login form using provided credentials.
        Navigates to the URL first, detects login fields, fills them, and submits.
        """
        logger.info("═══ Handling authentication...")
        await self._conn.send("Network.enable")

        # Check if there's a login form on the page
        has_login = await self._runtime.evaluate("""
            (() => {
                const inputs = document.querySelectorAll('input');
                let hasEmail = false, hasPassword = false;
                inputs.forEach(i => {
                    const ac = (i.getAttribute('autocomplete') || '').toLowerCase();
                    if (i.type === 'email' ||
                        (i.type === 'text' && (
                            i.name?.includes('email') || i.name?.includes('user') ||
                            i.placeholder?.toLowerCase().includes('email') ||
                            i.placeholder?.toLowerCase().includes('user') ||
                            ac.includes('email') || ac.includes('username')
                        ))
                    ) hasEmail = true;
                    if (i.type === 'password') hasPassword = true;
                });
                return hasEmail && hasPassword;
            })()
        """)

        if not has_login:
            # Try to find a navigation link to the login page
            navigated = await self._runtime.evaluate("""
                (() => {
                    const links = Array.from(document.querySelectorAll('a, button, [role="button"]'));
                    const loginNode = links.find(el => {
                        const t = (el.innerText || '').toLowerCase();
                        const h = (el.getAttribute('href') || '').toLowerCase();
                        return (
                            t.includes('log in') || t.includes('login') || t.includes('sign in') ||
                            t.includes('entrar') || t.includes('acessar') || h.includes('login') || h.includes('signin')
                        );
                    });
                    if (loginNode) {
                        loginNode.click();
                        return true;
                    }
                    return false;
                })()
            """)

            if navigated:
                logger.info("    Login link found, navigating to authentication page...")
                try:
                    await self._page.wait_for_network_idle(timeout=10.0, idle_time=0.5)
                except Exception:
                    pass

                has_login = await self._runtime.evaluate("""
                    (() => {
                        const inputs = document.querySelectorAll('input');
                        let hasE = false, hasP = false;
                        inputs.forEach(i => {
                            const ac = (i.getAttribute('autocomplete') || '').toLowerCase();
                            if (i.type === 'email' || (i.type === 'text' && (
                                i.name?.includes('email') || i.name?.includes('user') ||
                                i.placeholder?.toLowerCase().includes('email') || i.placeholder?.toLowerCase().includes('user') ||
                                ac.includes('email') || ac.includes('username')
                            ))) hasE = true;
                            if (i.type === 'password') hasP = true;
                        });
                        return hasE && hasP;
                    })()
                """)

        if not has_login:
            logger.info("    No login form detected, skipping auth")
            return

        logger.info("    Login form detected, filling credentials")

        safe_username = json.dumps(self.username)
        safe_password = json.dumps(self.password)

        # Find and fill email/username field using native setter for React compatibility
        email_filled = await self._runtime.evaluate(f"""
            (() => {{
                const nativeSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                const inputs = document.querySelectorAll('input');
                for (const i of inputs) {{
                    const ac = (i.getAttribute('autocomplete') || '').toLowerCase();
                    if (i.type === 'email' ||
                        (i.type === 'text' && (
                            i.name?.includes('email') || i.name?.includes('user') ||
                            i.placeholder?.toLowerCase().includes('email') ||
                            i.placeholder?.toLowerCase().includes('user') ||
                            ac.includes('email') || ac.includes('username')
                        ))
                    ) {{
                        i.focus();
                        i.select();
                        nativeSetter.call(i, {safe_username});
                        i.dispatchEvent(new InputEvent('input', {{
                            bubbles: true, cancelable: true,
                            inputType: 'insertText', data: {safe_username}
                        }}));
                        i.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        return true;
                    }}
                }}
                return false;
            }})()
        """)

        # Find and fill password field
        pass_filled = await self._runtime.evaluate(f"""
            (() => {{
                const nativeSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                const i = document.querySelector('input[type="password"]');
                if (i) {{
                    i.focus();
                    i.select();
                    nativeSetter.call(i, {safe_password});
                    i.dispatchEvent(new InputEvent('input', {{
                        bubbles: true, cancelable: true,
                        inputType: 'insertText', data: {safe_password}
                    }}));
                    i.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    return true;
                }}
                return false;
            }})()
        """)

        if email_filled and pass_filled:
            # Scope the submit button lookup to the form that owns the password
            # field so we don't accidentally click an unrelated button.
            submitted = await self._runtime.evaluate("""
                (() => {
                    const pw = document.querySelector('input[type="password"]');
                    const form = pw && (pw.form || pw.closest('form'));
                    const scope = form || document;
                    const btn = scope.querySelector(
                        'button[type="submit"], input[type="submit"], ' +
                        'button:not([type]), [role="button"]'
                    );
                    if (btn) { btn.click(); return 'button'; }
                    if (form) { form.submit(); return 'form'; }
                    return false;
                })()
            """)
            if not submitted:
                logger.warning(
                    "    Could not find a submit control for the login form; "
                    "skipping auth wait"
                )
                return
            logger.info(f"    Login submitted via: {submitted}")
            try:
                await self._conn.wait_for_event("Page.loadEventFired", timeout=10.0)
                await self._page.wait_for_network_idle(timeout=5.0, idle_time=0.5)
            except TimeoutError:
                logger.warning("    Auth redirect timed out, continuing anyway")
            logger.info("    Authentication complete")
        else:
            logger.warning("    Could not fill login form automatically")

    async def recover(self, url: str, llm: Optional[LLMClient] = None) -> None:
        """
        Recover from a catastrophic mid-execution browser crash or disconnect.
        Tears down stale state, respawns Chrome, re-authenticates, and navigates
        back to the URL where execution was interrupted.

        When ``llm`` is provided and credentials (``username``/``password``)
        are set, ``handle_login`` is called after navigation so the recovered
        session is fully authenticated rather than relying on possibly-expired
        cookies.
        """
        # P0-1: emit a structured log so the agent's run summary
        # can show "we recovered because the CDP circuit opened"
        # vs. "we recovered because Chrome crashed on its own".
        prior_state = self.cdp_state or "unknown"
        logger.error("⟲ Browser session completely lost. Attempting state machine recovery...")
        log_event("browser_recover_start", url=url, cdp_state=prior_state)

        # 1. Tear down stale processes
        await self.teardown()

        # 2. Respawn browser
        await self.setup()

        # 3. Enable network and inputs
        await self._page.enable()

        # 4. Re-inject auth cookies
        if self.cookies_file:
            await self.inject_cookies()

        # 5. Navigate back to where we crashed
        logger.info(f"⟲ State recovery navigating back to: {url}")
        await self._page.navigate(url, wait_for_network=True)

        # 6. Attempt full LLM-driven login if credentials are present.
        #    Surviving cookies may have expired during the crash, so we
        #    re-authenticate explicitly when an LLM client is available.
        if self.username and self.password and llm is not None:
            logger.info("⟲ Recovery: attempting login with provided credentials")
            await self.handle_login(llm)

        logger.info("⟲ State recovery complete. Re-attempting step.")
        log_event("browser_recover_complete", url=url, prior_cdp_state=prior_state)
