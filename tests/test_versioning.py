"""Tests for the versioning module (src/versioning.py)."""

import os
import pytest
from unittest.mock import patch, MagicMock
from src.versioning import (
    resolve_app_version,
    resolve_git_commit,
    resolve_deployed_at,
    get_versioning_context,
)


class TestResolveAppVersion:
    def test_explicit_value(self):
        assert resolve_app_version("2.3.1") == "2.3.1"

    def test_env_var_app_version(self, monkeypatch):
        monkeypatch.setenv("APP_VERSION", "1.0.0")
        assert resolve_app_version() == "1.0.0"

    def test_env_var_release_version(self, monkeypatch):
        monkeypatch.setenv("RELEASE_VERSION", "v3.0.0")
        assert resolve_app_version() == "v3.0.0"

    def test_env_var_unexpanded_github(self, monkeypatch):
        """Unexpanded $TAG_NAME should be ignored and fall through to git/default."""
        monkeypatch.setenv("TAG_NAME", "$TAG_NAME")
        # Since we're in a git repo, git describe returns a commit hash
        result = resolve_app_version()
        assert result != "$TAG_NAME"
        assert result != ""
        assert isinstance(result, str)

    def test_git_describe_fallback(self, monkeypatch):
        """Fallback to git describe when no env vars."""
        monkeypatch.delenv("APP_VERSION", raising=False)
        monkeypatch.delenv("RELEASE_VERSION", raising=False)
        monkeypatch.delenv("TAG_NAME", raising=False)
        # This may or may not work depending on git availability
        result = resolve_app_version()
        assert result != "$TAG_NAME"
        assert isinstance(result, str)
        # In a git repo without tags, it returns a short commit hash
        # In a non-repo env, it returns "0.0.0"
        assert len(result) > 0


class TestResolveGitCommit:
    def test_explicit_value(self):
        assert resolve_git_commit("abc1234") == "abc1234"

    def test_env_var_github_sha(self, monkeypatch):
        monkeypatch.setenv("GITHUB_SHA", "1234567890abcdef1234567890abcdef12345678")
        assert resolve_git_commit() == "1234567890abcdef1234567890abcdef12345678"

    def test_env_var_ci_commit_sha(self, monkeypatch):
        monkeypatch.setenv("CI_COMMIT_SHA", "abc1234")
        assert resolve_git_commit() == "abc1234"

    def test_short_sha_not_accepted(self, monkeypatch):
        monkeypatch.setenv("GITHUB_SHA", "12345")
        # Too short (< 7 chars), should fall through
        result = resolve_git_commit()
        assert result != "12345"

    def test_git_fallback(self, monkeypatch):
        monkeypatch.delenv("GIT_COMMIT", raising=False)
        monkeypatch.delenv("GITHUB_SHA", raising=False)
        result = resolve_git_commit()
        assert isinstance(result, str)


class TestResolveDeployedAt:
    def test_explicit_value(self):
        assert resolve_deployed_at("2026-05-09T14:00:00Z") == "2026-05-09T14:00:00Z"

    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("DEPLOYED_AT", "2026-01-01T00:00:00Z")
        assert resolve_deployed_at() == "2026-01-01T00:00:00Z"

    def test_default_to_now(self):
        result = resolve_deployed_at()
        assert result  # Should not be empty
        assert "T" in result  # ISO format contains T


class TestGetVersioningContext:
    def test_all_explicit(self):
        ctx = get_versioning_context(
            app_version="2.3.1",
            git_commit="abc1234",
            deployed_at="2026-05-09T14:00:00Z",
        )
        assert ctx == {
            "app_version": "2.3.1",
            "git_commit": "abc1234",
            "deployed_at": "2026-05-09T14:00:00Z",
        }

    def test_auto_resolution(self, monkeypatch):
        monkeypatch.setenv("APP_VERSION", "1.0.0")
        monkeypatch.setenv("GITHUB_SHA", "1234567890abcdef1234567890abcdef12345678")
        monkeypatch.setenv("DEPLOYED_AT", "2026-01-01T00:00:00Z")

        ctx = get_versioning_context()
        assert ctx["app_version"] == "1.0.0"
        assert ctx["git_commit"] == "1234567890abcdef1234567890abcdef12345678"
        assert ctx["deployed_at"] == "2026-01-01T00:00:00Z"
