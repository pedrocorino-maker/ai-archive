"""Tests for ai_archive.pipeline.dedupe."""
from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from ai_archive.db import upsert_conversation
from ai_archive.models import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
    Provider,
)
from ai_archive.pipeline.dedupe import (
    find_duplicates,
    is_duplicate,
    is_revision,
    mark_tombstone,
    snapshot_if_changed,
)


def _make_conv(
    conv_id: str,
    provider_id: str,
    title: str,
    messages_text: list[str],
    provider: Provider = Provider.CHATGPT,
) -> Conversation:
    msgs = [
        Message(
            role=MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT,
            raw_text=text,
            normalized_text=text,
            ordinal=i,
        )
        for i, text in enumerate(messages_text)
    ]
    conv = Conversation(
        id=conv_id,
        provider=provider,
        provider_conversation_id=provider_id,
        title=title,
        url=f"https://chatgpt.com/c/{provider_id}",
        extracted_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        messages=msgs,
        canonical_text=" ".join(messages_text),
    )
    conv.content_hash = conv.compute_hash()
    return conv


# ---------------------------------------------------------------------------
# is_duplicate
# ---------------------------------------------------------------------------

def test_is_duplicate_identical_conversations():
    conv_a = _make_conv("id1", "pid1", "Title", ["hello", "world"])
    conv_b = _make_conv("id2", "pid2", "Title", ["hello", "world"])
    # Same messages -> same hash
    assert is_duplicate(conv_a, conv_b)


def test_is_duplicate_different_conversations():
    conv_a = _make_conv("id1", "pid1", "Title", ["hello", "world"])
    conv_b = _make_conv("id2", "pid2", "Title", ["foo", "bar"])
    assert not is_duplicate(conv_a, conv_b)


def test_is_duplicate_empty_hash():
    conv_a = _make_conv("id1", "pid1", "Title", ["hello"])
    conv_b = _make_conv("id2", "pid2", "Title", ["hello"])
    conv_a.content_hash = ""
    assert not is_duplicate(conv_a, conv_b)


# ---------------------------------------------------------------------------
# is_revision
# ---------------------------------------------------------------------------

def test_is_revision_detects_similar():
    """Two nearly identical conversations should have high similarity."""
    text_a = "Python is a high-level programming language known for readability."
    text_b = "Python is a high-level programming language known for its readability."
    conv_a = _make_conv("id1", "pid1", "T", [text_a])
    conv_b = _make_conv("id2", "pid2", "T", [text_b])
    conv_a.canonical_text = text_a
    conv_b.canonical_text = text_b
    score = is_revision(conv_a, conv_b)
    assert score > 0.7


def test_is_revision_low_score_for_different():
    """Completely different conversations should have low similarity."""
    conv_a = _make_conv("id1", "pid1", "T", ["Python programming"])
    conv_b = _make_conv("id2", "pid2", "T", ["Cooking recipes for dinner"])
    conv_a.canonical_text = "Python programming"
    conv_b.canonical_text = "Cooking recipes for dinner"
    score = is_revision(conv_a, conv_b)
    assert score < 0.6


def test_is_revision_returns_float():
    conv_a = _make_conv("id1", "pid1", "T", ["hello"])
    conv_b = _make_conv("id2", "pid2", "T", ["world"])
    score = is_revision(conv_a, conv_b)
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


def test_is_revision_changed_conversation():
    """A conversation with an added message should be detected as a revision."""
    conv_a = _make_conv("id1", "pid1", "T", ["original content here", "assistant response"])
    conv_b = _make_conv("id2", "pid2", "T", ["original content here", "assistant response extended"])
    conv_a.canonical_text = "original content here assistant response"
    conv_b.canonical_text = "original content here assistant response extended more"
    score = is_revision(conv_a, conv_b)
    assert score > 0.5


# ---------------------------------------------------------------------------
# mark_tombstone
# ---------------------------------------------------------------------------

def test_mark_tombstone_sets_deleted_flag(tmp_db):
    conv = _make_conv("ts001", "pid_ts", "Tombstone Test", ["msg1", "msg2"])
    upsert_conversation(tmp_db, conv)

    mark_tombstone(tmp_db, "ts001", reason="test deletion")

    from ai_archive.db import list_conversations
    updated = [c for c in list_conversations(tmp_db) if c.id == "ts001"]
    assert len(updated) == 1
    assert updated[0].deleted_or_missing is True
    assert updated[0].status == ConversationStatus.DELETED


def test_mark_tombstone_preserves_data(tmp_db):
    """Tombstoning should keep the conversation data intact."""
    conv = _make_conv("ts002", "pid_ts2", "Keep Data Test", ["important message"])
    upsert_conversation(tmp_db, conv)

    mark_tombstone(tmp_db, "ts002", reason="duplicate")

    from ai_archive.db import list_conversations
    updated = [c for c in list_conversations(tmp_db) if c.id == "ts002"]
    assert updated[0].title == "Keep Data Test"


def test_mark_tombstone_nonexistent(tmp_db):
    """Marking a non-existent conversation should not raise."""
    mark_tombstone(tmp_db, "nonexistent_id", reason="test")  # should not raise


# ---------------------------------------------------------------------------
# find_duplicates
# ---------------------------------------------------------------------------

def test_find_duplicates_detects_identical(tmp_db):
    conv_a = _make_conv("dup1", "pid_dup1", "Title", ["same text here"])
    conv_b = _make_conv("dup2", "pid_dup2", "Title", ["same text here"])
    upsert_conversation(tmp_db, conv_a)
    upsert_conversation(tmp_db, conv_b)

    dups = find_duplicates(tmp_db)
    # Both convs have same hash -> should be detected
    assert len(dups) >= 1
    pair_ids = {frozenset(pair) for pair in dups}
    assert frozenset({"dup1", "dup2"}) in pair_ids


def test_find_duplicates_empty_db(tmp_db):
    dups = find_duplicates(tmp_db)
    assert dups == []


# ---------------------------------------------------------------------------
# snapshot_if_changed
# ---------------------------------------------------------------------------

def test_snapshot_if_changed_new_conversation(tmp_db):
    """First insertion should create a snapshot."""
    conv = _make_conv("snap1", "pid_snap1", "Snap Test", ["hello world"])
    snap = snapshot_if_changed(tmp_db, conv)
    assert snap is not None
    assert snap.content_hash == conv.content_hash


def test_snapshot_if_changed_no_change(tmp_db):
    """If hash unchanged, no snapshot should be created."""
    conv = _make_conv("snap2", "pid_snap2", "Snap Test 2", ["hello world"])
    upsert_conversation(tmp_db, conv)
    # First call: creates snapshot
    snapshot_if_changed(tmp_db, conv)
    # Second call with same hash: should return None
    snap = snapshot_if_changed(tmp_db, conv)
    assert snap is None
