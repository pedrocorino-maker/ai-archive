"""Tests for ai_archive.pipeline.curate."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from ai_archive.models import (
    CodeBlock,
    Conversation,
    Message,
    MessageRole,
    Provider,
    TopicCluster,
)
from ai_archive.pipeline.curate import CurationPipeline


def _make_settings():
    s = MagicMock()
    s.curation_llm_provider = "none"
    s.curated_dir = MagicMock()
    s.curated_dir.__truediv__ = lambda self, other: MagicMock()
    return s


def _make_msg(
    role: MessageRole,
    text: str,
    has_code: bool = False,
    ordinal: int = 0,
) -> Message:
    code_blocks = []
    if has_code:
        code_blocks = [CodeBlock(language="python", code="print('hello')", ordinal=0)]
    return Message(
        role=role,
        raw_text=text,
        normalized_text=text,
        code_blocks=code_blocks,
        ordinal=ordinal,
    )


def _make_conv(
    conv_id: str,
    messages: list[Message],
    provider: Provider = Provider.CHATGPT,
) -> Conversation:
    conv = Conversation(
        id=conv_id,
        provider=provider,
        provider_conversation_id=f"pid_{conv_id}",
        title=f"Title {conv_id}",
        extracted_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        messages=messages,
        canonical_text=" ".join(m.raw_text for m in messages),
    )
    conv.content_hash = conv.compute_hash()
    return conv


# ---------------------------------------------------------------------------
# score_message
# ---------------------------------------------------------------------------

def test_score_message_higher_for_code():
    pipeline = CurationPipeline(settings=_make_settings())
    msg_no_code = _make_msg(MessageRole.ASSISTANT, "Some explanation here.")
    msg_with_code = _make_msg(MessageRole.ASSISTANT, "Some explanation here.", has_code=True)

    score_no = pipeline.score_message(msg_no_code, recency_weight=0.5)
    score_yes = pipeline.score_message(msg_with_code, recency_weight=0.5)
    assert score_yes > score_no


def test_score_message_conclusion_boosts():
    pipeline = CurationPipeline(settings=_make_settings())
    msg_plain = _make_msg(MessageRole.ASSISTANT, "Some generic response text.")
    msg_conclusion = _make_msg(MessageRole.ASSISTANT, "Therefore, the solution is to use async/await.")

    score_plain = pipeline.score_message(msg_plain, recency_weight=0.0)
    score_conc = pipeline.score_message(msg_conclusion, recency_weight=0.0)
    assert score_conc >= score_plain


def test_score_message_in_range():
    pipeline = CurationPipeline(settings=_make_settings())
    msg = _make_msg(MessageRole.ASSISTANT, "test message", has_code=True)
    score = pipeline.score_message(msg, recency_weight=1.0)
    assert 0.0 <= score <= 1.0


def test_score_message_recency_weight():
    pipeline = CurationPipeline(settings=_make_settings())
    msg = _make_msg(MessageRole.ASSISTANT, "same text")
    score_low = pipeline.score_message(msg, recency_weight=0.0)
    score_high = pipeline.score_message(msg, recency_weight=1.0)
    assert score_high > score_low


# ---------------------------------------------------------------------------
# select_best_content
# ---------------------------------------------------------------------------

def _sample_conversations() -> list[Conversation]:
    convs = []
    for i in range(3):
        msgs = [
            _make_msg(MessageRole.USER, f"Question {i}: how do I do thing {i}?", ordinal=0),
            _make_msg(
                MessageRole.ASSISTANT,
                f"Answer {i}: Therefore, the solution is to do step {i}.",
                has_code=(i % 2 == 0),
                ordinal=1,
            ),
        ]
        convs.append(_make_conv(f"conv{i}", msgs))
    return convs


def test_select_best_content_returns_all_keys():
    pipeline = CurationPipeline(settings=_make_settings())
    convs = _sample_conversations()
    result = pipeline.select_best_content(convs)

    required_keys = [
        "executive_summary",
        "decisions_conclusions",
        "best_content",
        "useful_prompts",
        "code_snippets",
        "contradictions",
        "open_questions",
    ]
    for key in required_keys:
        assert key in result, f"Missing key: {key}"


def test_select_best_content_open_questions():
    """Messages ending in '?' should appear in open_questions."""
    pipeline = CurationPipeline(settings=_make_settings())
    msgs = [
        _make_msg(MessageRole.USER, "What is the best way to do this?", ordinal=0),
        _make_msg(MessageRole.ASSISTANT, "You should use X.", ordinal=1),
    ]
    conv = _make_conv("qconv", msgs)
    result = pipeline.select_best_content([conv])
    assert len(result["open_questions"]) >= 1


def test_select_best_content_no_open_questions_without_q():
    """Non-question messages should not appear in open_questions."""
    pipeline = CurationPipeline(settings=_make_settings())
    msgs = [
        _make_msg(MessageRole.USER, "Tell me about Python.", ordinal=0),
        _make_msg(MessageRole.ASSISTANT, "Python is a language.", ordinal=1),
    ]
    conv = _make_conv("noqconv", msgs)
    result = pipeline.select_best_content([conv])
    assert len(result["open_questions"]) == 0


def test_code_snippet_deduplication():
    """Identical code blocks should only appear once in code_snippets."""
    pipeline = CurationPipeline(settings=_make_settings())
    same_code = CodeBlock(language="python", code="print('same code')", ordinal=0)
    msgs_a = [_make_msg(MessageRole.ASSISTANT, "Answer A", ordinal=0)]
    msgs_b = [_make_msg(MessageRole.ASSISTANT, "Answer B", ordinal=0)]
    msgs_a[0].code_blocks = [same_code]
    msgs_b[0].code_blocks = [same_code]

    conv_a = _make_conv("ca", msgs_a)
    conv_b = _make_conv("cb", msgs_b)

    result = pipeline.select_best_content([conv_a, conv_b])
    # Should deduplicate: only 1 unique code snippet
    assert len(result["code_snippets"]) == 1


def test_select_best_content_empty_conversations():
    pipeline = CurationPipeline(settings=_make_settings())
    result = pipeline.select_best_content([])
    assert result["best_content"] == []
    assert result["code_snippets"] == []
