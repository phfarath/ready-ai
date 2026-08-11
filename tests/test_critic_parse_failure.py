"""
Tests for critic parse-failure default (VAL-ROB-010).

critic.review() must default to is_complete=False when the LLM response
cannot be parsed as JSON.  Previously it defaulted to True, which masked
parse failures as "approved" and caused the loop to stop improving.

Valid JSON must continue to work unchanged (no regression).
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.critic import CriticFeedback, review  # noqa: E402


def _make_llm(response_text: str):
    """Build a mock LLM client whose complete() returns response_text."""
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=response_text)
    return llm


@pytest.mark.asyncio
async def test_malformed_json_defaults_to_not_complete():
    """Malformed JSON from LLM must return is_complete=False (not True)."""
    llm = _make_llm("this is not valid json {{{")
    feedback = await review("# Some docs", "Document the login flow", llm)
    assert isinstance(feedback, CriticFeedback)
    assert feedback.is_complete is False


@pytest.mark.asyncio
async def test_valid_json_is_complete_true():
    """Valid JSON with is_complete=true must still return True (no regression)."""
    llm = _make_llm(
        json.dumps(
            {
                "is_complete": True,
                "score": 9,
                "feedback": "Great docs",
                "missing_steps": [],
                "suggestions": [],
            }
        )
    )
    feedback = await review("# Some docs", "Document the login flow", llm)
    assert feedback.is_complete is True
    assert feedback.score == 9


@pytest.mark.asyncio
async def test_valid_json_is_complete_false():
    """Valid JSON with is_complete=false must return False."""
    llm = _make_llm(
        json.dumps(
            {
                "is_complete": False,
                "score": 3,
                "feedback": "Missing steps",
                "missing_steps": ["step1"],
                "suggestions": ["add more detail"],
            }
        )
    )
    feedback = await review("# Some docs", "Document the login flow", llm)
    assert feedback.is_complete is False
    assert feedback.score == 3
    assert feedback.missing_steps == ["step1"]
