"""
Tests for the WAF/bot challenge page detector.

navigate() must fail loud (ChallengePageError) when the settled document
is a Cloudflare/DataDome-style interstitial, and fail open when the probe
itself is unavailable. The pure detector is covered exhaustively; navigate
integration uses mocked CDPConnection like the neighboring test files.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cdp.connection import CDPConnection
from src.cdp.exceptions import ChallengePageError, WebSocketDisconnected
from src.cdp.page import (
    CHALLENGE_HEURISTIC_MATCH,
    CHALLENGE_SIGNATURES,
    ENV_CHALLENGE_DETECT,
    ENV_CHALLENGE_SIZE_FLOOR,
    PageDomain,
    detect_challenge_page,
)


PAD = "<!-- filler content to emulate a realistic interstitial payload -->"


def _padded_html(core: str, target_chars: int) -> str:
    padding = PAD * (max(target_chars - len(core), 0) // len(PAD) + 1)
    return f"{core}{padding}"[: max(target_chars, len(core))]


def _cloudflare_html() -> tuple[str, str]:
    core = (
        "<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
        "<body><div class=\"main-wrapper\"><h1>Checking your browser</h1>"
        "<div id=\"cf-turnstile\" data-sitekey=\"0x4AAAAAAA\"></div>"
        "<form class=\"challenge-form\" action=\"/cdn-cgi/l/challenge_js/verify\" method=\"POST\">"
        "<input type=\"hidden\" name=\"md\" value=\"pad\"/>"
        "</form><p>Enable JavaScript and cookies to continue</p></div>"
        "<script src=\"/cdn-cgi/challenge-platform/scripts/jsd/main.js\"></script>"
    )
    return _padded_html(core, 27_000), "Just a moment..."


def _datadome_html() -> tuple[str, str]:
    core = (
        "<!DOCTYPE html><html><head><title>datadome</title></head>"
        "<body><div class=\"dds__container\"><h1>Press &amp; Hold</h1>"
        "<p>Complete the verification to continue to the site.</p>"
        "<div id=\"ddv1-press-hold-button\" class=\"dds__button\"></div>"
        "<img src=\"https://api-js.datadome.co/captcha/\" alt=\"datadome captcha\"/>"
        "</div></body></html>"
    )
    return _padded_html(core, 25_000), "datadome"


def _normal_html(target_chars: int = 27_000) -> tuple[str, str]:
    nav_items = "".join(
        f"<li><a href='/section-{i}/reports'>Section {i} reports</a></li>" for i in range(12)
    )
    rows = "".join(
        "<tr><td class='cell'>Invoice 2026-000%d</td><td class='cell status'>paid</td></tr>"
        % i
        for i in range(40)
    )
    core = (
        "<!DOCTYPE html><html><head><title>Acme Dashboard - Invoices</title></head>"
        "<body><header><nav><ul>" + nav_items + "</ul></nav></header>"
        "<main><h1>Invoices</h1><table id='invoice-grid'><tbody>" + rows + "</tbody></table>"
        "<form action='/invoices/search' method='GET'><input name='q' type='search'/>"
        "<button type='submit'>Search</button></form></main>"
        "<footer><a href='/help'>Help center</a></footer></body></html>"
    )
    return _padded_html(core, target_chars), "Acme Dashboard - Invoices"


def _page_with_document(html: str, title: str):
    def fake_send(method, params=None, session_id=None, timeout=30.0):
        if method == "Runtime.evaluate":
            return {"result": {"value": {"title": title, "html": html}}}
        return {}

    conn = CDPConnection()
    conn._ws = AsyncMock()
    conn.send = AsyncMock(side_effect=fake_send)
    return PageDomain(conn), conn


class TestChallengePageError:
    def test_is_runtime_error_not_disconnect(self):
        assert issubclass(ChallengePageError, RuntimeError)
        assert not issubclass(ChallengePageError, WebSocketDisconnected)

    def test_carries_context(self):
        exc = ChallengePageError(
            "blocked", signature="just a moment", url="https://x.com/", title="Just a moment..."
        )
        assert exc.signature == "just a moment"
        assert exc.url == "https://x.com/"
        assert exc.title == "Just a moment..."
        assert "blocked" in str(exc)


class TestDetectChallengePagePure:
    @pytest.mark.parametrize("signature", CHALLENGE_SIGNATURES)
    def test_each_signature_matches(self, signature):
        html = f"<html><body>{signature}</body></html>"
        assert detect_challenge_page(html) == signature

    def test_case_insensitive(self):
        html = "<HTML><BODY>JUST A MOMENT... Please CF-TURNSTILE</BODY></HTML>"
        assert detect_challenge_page(html) == "just a moment"
        assert detect_challenge_page("Verify You Are Human", title="") == "verify you are human"

    def test_matches_in_title_only_when_body_clean_but_small(self):
        # Title fragments alone are NOT enough without the size heuristic.
        assert detect_challenge_page("<html></html>", title="Access Denied") is None

    def test_normal_page_does_not_match(self):
        html, title = _normal_html()
        assert detect_challenge_page(html, title=title) is None

    def test_empty_html_returns_none(self):
        assert detect_challenge_page("") is None

    def test_heuristic_suspicious_title_plus_size(self):
        big = "<html>" + "x" * 21_000 + "</html>"
        assert (
            detect_challenge_page(big, title="Attention Required! | Cloudflare")
            == CHALLENGE_HEURISTIC_MATCH
        )

    def test_heuristic_requires_size_floor(self):
        small = "<html>suspicious but tiny</html>"
        assert detect_challenge_page(small, title="Attention Required!") is None

    def test_heuristic_requires_suspicious_title(self):
        big = "<html>" + "y" * 21_000 + "</html>"
        assert detect_challenge_page(big, title="Totally Normal Page") is None

    def test_size_floor_env_adjustable(self, monkeypatch):
        monkeypatch.setenv(ENV_CHALLENGE_SIZE_FLOOR, "10")
        assert (
            detect_challenge_page("<html>tiny</html>", title="Press & Hold")
            == CHALLENGE_HEURISTIC_MATCH
        )

    def test_invalid_size_floor_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv(ENV_CHALLENGE_SIZE_FLOOR, "garbage")
        big = "<html>" + "z" * 21_000 + "</html>"
        assert detect_challenge_page(big, title="Access Denied") == CHALLENGE_HEURISTIC_MATCH
        assert detect_challenge_page("<html>t</html>", title="Access Denied") is None


class TestNavigateRaisesOnBlockPages:
    @pytest.mark.asyncio
    async def test_cloudflare_block_page_raises(self):
        html, title = _cloudflare_html()
        page, conn = _page_with_document(html, title)
        with pytest.raises(ChallengePageError) as exc_info:
            await page.navigate(
                "https://target.example.com/app?token=secret",
                wait_for_load=False,
                wait_for_network=False,
            )
        exc = exc_info.value
        assert exc.signature == "just a moment"
        assert exc.title == "Just a moment..."
        assert exc.url == "https://target.example.com/app"
        assert "token=secret" not in str(exc)
        methods = [c.args[0] for c in conn.send.await_args_list]
        assert methods[0] == "Page.navigate"

    @pytest.mark.asyncio
    async def test_datadome_block_page_raises(self):
        html, title = _datadome_html()
        page, _ = _page_with_document(html, title)
        with pytest.raises(ChallengePageError) as exc_info:
            await page.navigate(
                "https://shop.example.com/checkout",
                wait_for_load=False,
                wait_for_network=False,
            )
        assert exc_info.value.signature == "datadome"
        assert exc_info.value.url == "https://shop.example.com/checkout"

    @pytest.mark.asyncio
    async def test_error_message_names_matched_signature(self):
        html, title = _cloudflare_html()
        page, _ = _page_with_document(html, title)
        with pytest.raises(ChallengePageError, match="just a moment"):
            await page.navigate(
                "https://target.example.com/",
                wait_for_load=False,
                wait_for_network=False,
            )


class TestNavigateNormalPage:
    @pytest.mark.asyncio
    async def test_large_normal_page_navigates_without_exception(self):
        html, title = _normal_html()
        page, conn = _page_with_document(html, title)
        await page.navigate(
            "https://app.example.com/invoices",
            wait_for_load=False,
            wait_for_network=False,
        )
        methods = [c.args[0] for c in conn.send.await_args_list]
        assert methods.count("Runtime.evaluate") == 1

    @pytest.mark.asyncio
    async def test_small_normal_page_navigates(self):
        html = "<html><head><title>Home</title></head><body><p>welcome</p></body></html>"
        page, conn = _page_with_document(html, "Home")
        await page.navigate(
            "https://example.com/", wait_for_load=False, wait_for_network=False
        )
        methods = [c.args[0] for c in conn.send.await_args_list]
        assert methods.count("Runtime.evaluate") == 1


class TestNavigateFailOpen:
    @pytest.mark.asyncio
    async def test_probe_exception_is_swallowed(self):
        conn = CDPConnection()
        conn._ws = AsyncMock()

        def fake_send(method, params=None, session_id=None, timeout=30.0):
            if method == "Runtime.evaluate":
                raise RuntimeError("evaluate exploded")
            return {}

        conn.send = AsyncMock(side_effect=fake_send)
        page = PageDomain(conn)
        await page.navigate(
            "https://example.com/", wait_for_load=False, wait_for_network=False
        )

    @pytest.mark.asyncio
    async def test_probe_none_value_is_swallowed(self):
        conn = CDPConnection()
        conn._ws = AsyncMock()
        conn.send = AsyncMock(return_value={})
        page = PageDomain(conn)
        await page.navigate(
            "https://example.com/", wait_for_load=False, wait_for_network=False
        )


class TestNavigateOptOut:
    @pytest.mark.asyncio
    async def test_check_challenge_false_skips_probe_even_on_block_page(self):
        html, title = _cloudflare_html()
        page, conn = _page_with_document(html, title)
        await page.navigate(
            "https://target.example.com/",
            wait_for_load=False,
            wait_for_network=False,
            check_challenge=False,
        )
        methods = [c.args[0] for c in conn.send.await_args_list]
        assert "Runtime.evaluate" not in methods

    @pytest.mark.asyncio
    async def test_env_var_zero_disables_detection(self, monkeypatch):
        monkeypatch.setenv(ENV_CHALLENGE_DETECT, "0")
        html, title = _cloudflare_html()
        page, conn = _page_with_document(html, title)
        await page.navigate(
            "https://target.example.com/",
            wait_for_load=False,
            wait_for_network=False,
        )
        methods = [c.args[0] for c in conn.send.await_args_list]
        assert "Runtime.evaluate" not in methods

    @pytest.mark.asyncio
    async def test_env_var_false_disables_detection(self, monkeypatch):
        monkeypatch.setenv(ENV_CHALLENGE_DETECT, "false")
        html, title = _cloudflare_html()
        page, _ = _page_with_document(html, title)
        await page.navigate(
            "https://target.example.com/",
            wait_for_load=False,
            wait_for_network=False,
        )

    @pytest.mark.asyncio
    async def test_env_var_true_keeps_detection(self, monkeypatch):
        monkeypatch.setenv(ENV_CHALLENGE_DETECT, "true")
        html, title = _cloudflare_html()
        page, _ = _page_with_document(html, title)
        with pytest.raises(ChallengePageError):
            await page.navigate(
                "https://target.example.com/",
                wait_for_load=False,
                wait_for_network=False,
            )
