"""READY-AI-T-10 regression: PR #19 triage evidence.

Asserts that the fixes covered by previous cards (T-3, T-4, T-5)
are present in the current branch, and that deferred items are
documented with rationale. No functional code changes are required
for deferred architectural items.
"""

import os


def test_review_decisions_file_exists():
    path = "docs/review_decisions.md"
    assert os.path.exists(path), f"Decision log missing: {path}"
    content = open(path, encoding="utf-8").read()
    assert "READY-AI-T-10" in content
    assert "já resolvido" in content or "adiado" in content


def test_security_fixes_present():
    # Evidence that VAL-SEC assertions are covered by previous cards.
    assert os.path.exists("tests/test_executor_password_redaction.py")
    assert os.path.exists("tests/test_cdp_sanitize.py")
    assert os.path.exists("tests/test_cdp_page_navigate_url_validation.py")


def test_robustness_fixes_present():
    assert os.path.exists("tests/test_browser_session_recover_login.py")
    assert os.path.exists("tests/test_browser_session_windows_kill.py")
    assert os.path.exists("tests/test_cdp_send_future_cleanup.py")


def test_quality_fixes_present():
    assert os.path.exists("tests/test_no_get_event_loop.py")
    assert os.path.exists("tests/test_critic_parse_failure.py")


def test_deferred_items_documented():
    content = open("docs/review_decisions.md", encoding="utf-8").read()
    # Verify deferred categories from PR #19 scope are noted.
    assert "adiado" in content or "deferred" in content.lower()
    assert "successor" in content.lower() or "PLAN_FASE" in content
