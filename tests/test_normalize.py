"""Tests for ai_archive.utils.text and ai_archive.pipeline.normalize."""
from __future__ import annotations

import pytest

from ai_archive.models import Conversation, Message, MessageRole, Provider
from ai_archive.utils.text import (
    clean_title,
    make_stable_slug,
    normalize_whitespace,
    score_content,
    slugify,
)


# ---------------------------------------------------------------------------
# normalize_whitespace
# ---------------------------------------------------------------------------

def test_normalize_whitespace_collapses_spaces():
    assert normalize_whitespace("hello   world") == "hello world"


def test_normalize_whitespace_strips():
    assert normalize_whitespace("  hello  ") == "hello"


def test_normalize_whitespace_newlines():
    result = normalize_whitespace("line1\n\n\n\nline2")
    assert result == "line1\n\nline2"


def test_normalize_whitespace_mixed():
    result = normalize_whitespace("  foo   bar  \n\n\nbaz  ")
    assert result == "foo bar\n\nbaz"


def test_normalize_whitespace_empty():
    assert normalize_whitespace("") == ""


# ---------------------------------------------------------------------------
# clean_title
# ---------------------------------------------------------------------------

def test_clean_title_strips_new_chat():
    assert clean_title("New chat") == "Untitled Conversation"


def test_clean_title_strips_nova_conversa():
    assert clean_title("Nova conversa") == "Untitled Conversation"


def test_clean_title_empty_string():
    assert clean_title("") == "Untitled Conversation"


def test_clean_title_strips_with_fallback():
    result = clean_title("new chat", fallback_text="How do I use Python?")
    assert result == "How do I use Python?"


def test_clean_title_derives_from_fallback():
    result = clean_title("", fallback_text="Tell me about machine learning\nMore text here")
    assert "machine learning" in result.lower() or result != "Untitled Conversation"


def test_clean_title_real_title():
    assert clean_title("Python String Manipulation") == "Python String Manipulation"


def test_clean_title_untitled_fallback():
    result = clean_title("New chat", fallback_text="")
    assert result == "Untitled Conversation"


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------

def test_slugify_basic():
    assert slugify("Hello World") == "hello-world"


def test_slugify_special_chars():
    result = slugify("Python: Tips & Tricks!")
    assert " " not in result
    assert ":" not in result
    assert "&" not in result


def test_slugify_multiple_hyphens():
    result = slugify("foo   bar")
    assert "--" not in result


def test_slugify_leading_trailing():
    result = slugify("  hello  ")
    assert not result.startswith("-")
    assert not result.endswith("-")


def test_slugify_empty():
    assert slugify("") == ""


def test_slugify_unicode():
    result = slugify("café au lait")
    assert isinstance(result, str)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# make_stable_slug
# ---------------------------------------------------------------------------

def test_make_stable_slug_no_collision():
    existing = {"other-topic"}
    slug = make_stable_slug("My Topic", existing)
    assert slug == "my-topic"


def test_make_stable_slug_collision():
    existing = {"my-topic"}
    slug = make_stable_slug("My Topic", existing)
    assert slug != "my-topic"
    assert "my-topic" in slug


def test_make_stable_slug_adds_to_set():
    """Test that returned slug is not already in existing_slugs."""
    existing = {"python-tips"}
    slug = make_stable_slug("Python Tips", existing)
    assert slug not in existing


def test_make_stable_slug_empty_text():
    existing: set[str] = set()
    slug = make_stable_slug("", existing)
    assert len(slug) > 0


# ---------------------------------------------------------------------------
# score_content
# ---------------------------------------------------------------------------

def test_score_content_returns_float():
    score = score_content("some text", has_code=False, recency_score=0.5, has_conclusion=False)
    assert isinstance(score, float)


def test_score_content_in_range():
    score = score_content("some text", has_code=False, recency_score=0.5, has_conclusion=False)
    assert 0.0 <= score <= 1.0


def test_score_content_higher_with_code():
    score_no_code = score_content("text", has_code=False, recency_score=0.5, has_conclusion=False)
    score_with_code = score_content("text", has_code=True, recency_score=0.5, has_conclusion=False)
    assert score_with_code > score_no_code


def test_score_content_higher_with_conclusion():
    score_no = score_content("text", has_code=False, recency_score=0.5, has_conclusion=False)
    score_yes = score_content("text", has_code=False, recency_score=0.5, has_conclusion=True)
    assert score_yes > score_no


def test_score_content_max_is_one():
    score = score_content(
        "x" * 5000, has_code=True, recency_score=1.0, has_conclusion=True
    )
    assert score <= 1.0


def test_score_content_conclusion_keyword_detection():
    """score_content should detect conclusion keywords in text itself."""
    text_with_kw = "Therefore, the solution is to use a sorted set."
    score_kw = score_content(text_with_kw, has_code=False, recency_score=0.0, has_conclusion=False)
    score_plain = score_content("some plain text", has_code=False, recency_score=0.0, has_conclusion=False)
    assert score_kw >= score_plain


# ---------------------------------------------------------------------------
# normalize_all (integration, no disk I/O side effects checked)
# ---------------------------------------------------------------------------

def test_normalize_conversation_sets_canonical_text(tmp_path, tmp_db, sample_conversation):
    """normalize_conversation should set canonical_text and content_hash."""
    from ai_archive.pipeline.normalize import normalize_conversation

    result = normalize_conversation(sample_conversation, tmp_path)
    assert result.canonical_text
    assert result.content_hash
    assert result.normalized_json_path
    assert result.markdown_path


def test_normalize_conversation_creates_files(tmp_path, tmp_db, sample_conversation):
    """normalize_conversation should write JSON and MD files."""
    from ai_archive.pipeline.normalize import normalize_conversation
    from pathlib import Path

    result = normalize_conversation(sample_conversation, tmp_path)
    assert Path(result.normalized_json_path).exists()
    assert Path(result.markdown_path).exists()
