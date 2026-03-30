"""
Browser Session — Chrome lifecycle, authentication, and crash recovery.

Encapsulates launching Chrome, CDP connection management, cookie injection,
login form handling, and browser crash recovery.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from ..cdp.browser import launch_chrome, get_ws_url
from ..cdp.connection import CDPConnection
from ..cdp.page import PageDomain
from ..cdp.input import InputDomain
from ..cdp.runtime import RuntimeDomain
from ..llm.client import LLMClient

logger = logging.getLogger(__name__)


class BrowserSession:
    """Owns the full Chrome browser lifecycle."""

    def __init__(
        self,
        port: int = 9222,
        headless: bool = False,
        cookies_file: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        self.port = port
        self.headless = headless
        self.cookies_file = cookies_file
        self.username = username
        self.password = password

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

    async def setup(self) -> None:
        """Launch Chrome and establish CDP connection."""
        logger.info("Launching Chrome...")
        self._chrome_proc = launch_chrome(
            port=self.port,
            headless=self.headless,
        )
        ws_url = await get_ws_url(port=self.port)
        self._conn = CDPConnection()
        await self._conn.connect(ws_url)
        await self._conn.attach_to_page()
        self._init_domains()

    def _init_domains(self) -> None:
        """Create CDP domain helpers from current connection."""
        self._page = PageDomain(self._conn)
        self._input = InputDomain(self._conn)
        self._runtime = RuntimeDomain(self._conn)

    async def teardown(self) -> None:
        """Close connections and kill Chrome process."""
        if self._conn:
            try:
                await self._conn.close()
            except Exception:
                pass

        if self._chrome_proc:
            try:
                self._chrome_proc.terminate()
                self._chrome_proc.wait(timeout=5)
            except Exception:
                try:
                    self._chrome_proc.kill()
                except Exception:
                    pass
            logger.info("Chrome process terminated")

    async def inject_cookies(self) -> None:
        """Inject cookies from a JSON file for session authentication."""
        if not self.cookies_file:
            return

        cookie_path = Path(self.cookies_file)
        if not cookie_path.exists():
            logger.error(f"Cookies file not found: {self.cookies_file}")
            return

        try:
            cookies = json.loads(cookie_path.read_text())
            if not isinstance(cookies, list):
                logger.error("Cookies file must contain a JSON array of cookie objects")
                return

            for cookie in cookies:
                if "name" not in cookie or "value" not in cookie:
                    continue

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
                if "expirationDate" in cookie:
                    cdp_cookie["expires"] = cookie["expirationDate"]

                await self._conn.send("Network.setCookie", cdp_cookie)

            logger.info(f"Injected {len(cookies)} cookies from {self.cookies_file}")

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
            submitted = await self._runtime.evaluate("""
                (() => {
                    const btn = document.querySelector(
                        'button[type="submit"], input[type="submit"], ' +
                        'button:not([type]), [role="button"]'
                    );
                    if (btn) { btn.click(); return 'button'; }
                    const form = document.querySelector('form');
                    if (form) { form.submit(); return 'form'; }
                    return false;
                })()
            """)
            logger.info(f"    Login submitted via: {submitted}")
            try:
                await self._conn.wait_for_event("Page.loadEventFired", timeout=10.0)
                await self._page.wait_for_network_idle(timeout=5.0, idle_time=0.5)
            except TimeoutError:
                logger.warning("    Auth redirect timed out, continuing anyway")
            logger.info("    Authentication complete")
        else:
            logger.warning("    Could not fill login form automatically")

    async def recover(self, url: str) -> None:
        """
        Recover from a catastrophic mid-execution browser crash or disconnect.
        Tears down stale state, respawns Chrome, re-authenticates, and navigates
        back to the URL where execution was interrupted.
        """
        logger.error("⟲ Browser session completely lost. Attempting state machine recovery...")

        # 1. Tear down stale processes
        await self.teardown()

        # 2. Respawn browser
        await self.setup()

        # 3. Enable network and inputs
        await self._page.enable()

        # 4. Re-inject auth
        if self.cookies_file:
            await self.inject_cookies()
        if self.username and self.password:
            logger.warning("Recovery: Skipping full LLM-driven login; relying on surviving cached cookies.")

        # 5. Navigate back to where we crashed
        logger.info(f"⟲ State recovery navigating back to: {url}")
        await self._page.navigate(url, wait_for_network=True)
        logger.info("⟲ State recovery complete. Re-attempting step.")
