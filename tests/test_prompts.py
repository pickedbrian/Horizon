"""Prompt regression tests."""

from src.ai.prompts import CONTENT_ANALYSIS_SYSTEM


def test_content_analysis_prompt_is_ai_focused():
    assert "focused AI/ML daily digest" in CONTENT_ANALYSIS_SYSTEM
    assert "non-AI items should rarely score above 4" in CONTENT_ANALYSIS_SYSTEM
    assert "general software engineering" in CONTENT_ANALYSIS_SYSTEM
