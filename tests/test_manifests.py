"""Tests for ai_archive.pipeline.manifests."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_archive.models import Conversation, Message, MessageRole, Provider
from ai_archive.pipeline.manifests import (
    build_manifest,
    compute_manifest_hash,
    read_manifest,
    write_manifest,
)


def _make_conv() -> Conversation:
    msg = Message(
        role=MessageRole.USER,
        raw_text="Test message",
        normalized_text="Test message",
        ordinal=0,
    )
    conv = Conversation(
        id="manifest_test_001",
        provider=Provider.CHATGPT,
        provider_conversation_id="pid_manifest001",
        title="Manifest Test Conversation",
        url="https://chatgpt.com/c/pid_manifest001",
        extracted_at=datetime(2024, 6, 15, tzinfo=timezone.utc),
        model_name="gpt-4o",
        messages=[msg],
        tags=["test", "manifest"],
        primary_topic_id="topic_abc",
        primary_topic_slug="test-topic",
    )
    conv.content_hash = conv.compute_hash()
    return conv


# ---------------------------------------------------------------------------
# build_manifest
# ---------------------------------------------------------------------------

def test_build_manifest_contains_required_fields():
    conv = _make_conv()
    manifest = build_manifest(conv)

    required = [
        "conversation_id",
        "provider",
        "provider_conversation_id",
        "title",
        "url",
        "extracted_at",
        "content_hash",
        "message_count",
        "manifest_version",
        "generated_at",
    ]
    for field in required:
        assert field in manifest, f"Missing field: {field}"


def test_build_manifest_values():
    conv = _make_conv()
    manifest = build_manifest(conv)

    assert manifest["conversation_id"] == "manifest_test_001"
    assert manifest["provider"] == "chatgpt"
    assert manifest["title"] == "Manifest Test Conversation"
    assert manifest["message_count"] == 1
    assert manifest["content_hash"] == conv.content_hash


def test_build_manifest_tags():
    conv = _make_conv()
    manifest = build_manifest(conv)
    assert manifest["tags"] == ["test", "manifest"]


# ---------------------------------------------------------------------------
# write/read manifest roundtrip
# ---------------------------------------------------------------------------

def test_write_read_manifest_roundtrip(tmp_path):
    conv = _make_conv()
    # Set a raw_html_path so write_manifest knows where to write
    raw_dir = tmp_path / "raw" / "chatgpt" / "2024" / "06"
    raw_dir.mkdir(parents=True, exist_ok=True)
    conv.raw_html_path = str(raw_dir / "pid_manifest001.html")

    manifest_path = write_manifest(conv, tmp_path)
    assert manifest_path.exists()

    loaded = read_manifest(manifest_path)
    assert loaded["conversation_id"] == "manifest_test_001"
    assert loaded["provider"] == "chatgpt"
    assert loaded["title"] == "Manifest Test Conversation"


def test_write_manifest_filename(tmp_path):
    conv = _make_conv()
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    conv.raw_html_path = str(raw_dir / "pid_manifest001.html")

    manifest_path = write_manifest(conv, tmp_path)
    assert manifest_path.name == "pid_manifest001__manifest.json"


# ---------------------------------------------------------------------------
# compute_manifest_hash
# ---------------------------------------------------------------------------

def test_manifest_hash_deterministic():
    conv = _make_conv()
    m1 = build_manifest(conv)
    m2 = build_manifest(conv)
    # Hashes should be equal regardless of generated_at
    h1 = compute_manifest_hash(m1)
    h2 = compute_manifest_hash(m2)
    assert h1 == h2


def test_manifest_hash_changes_with_content():
    conv = _make_conv()
    manifest_before = build_manifest(conv)
    hash_before = compute_manifest_hash(manifest_before)

    # Modify content
    conv.title = "Different Title Now"
    manifest_after = build_manifest(conv)
    hash_after = compute_manifest_hash(manifest_after)

    assert hash_before != hash_after


def test_manifest_hash_length():
    conv = _make_conv()
    manifest = build_manifest(conv)
    h = compute_manifest_hash(manifest)
    assert len(h) == 20
    assert all(c in "0123456789abcdef" for c in h)
