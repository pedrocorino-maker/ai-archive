"""AI Archive — DedupePipeline: detect duplicates and manage tombstones."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from rapidfuzz import fuzz

from ..db import list_conversations, upsert_conversation, upsert_snapshot
from ..logging_config import get_logger
from ..models import Conversation, ConversationSnapshot, ConversationStatus
from ..utils.time import utcnow

logger = get_logger("pipeline.dedupe")


def is_duplicate(conv_a: Conversation, conv_b: Conversation) -> bool:
    """Return True if both conversations have an identical content hash."""
    if not conv_a.content_hash or not conv_b.content_hash:
        return False
    return conv_a.content_hash == conv_b.content_hash


def is_revision(conv_a: Conversation, conv_b: Conversation) -> float:
    """Return similarity score 0–1 between two conversations.

    Score > 0.9 indicates one is likely a revised version of the other.
    Uses rapidfuzz token_sort_ratio on canonical texts.
    """
    text_a = conv_a.canonical_text or conv_a.title
    text_b = conv_b.canonical_text or conv_b.title
    if not text_a or not text_b:
        return 0.0
    ratio = fuzz.token_sort_ratio(text_a, text_b) / 100.0
    return ratio


def find_duplicates(
    db_conn: sqlite3.Connection,
) -> list[tuple[str, str]]:
    """Return list of (id_a, id_b) pairs that are exact duplicates."""
    convs = list_conversations(db_conn)
    seen: dict[str, str] = {}  # content_hash -> first id
    duplicates: list[tuple[str, str]] = []
    for conv in convs:
        if not conv.content_hash:
            continue
        if conv.content_hash in seen:
            duplicates.append((seen[conv.content_hash], conv.id))
        else:
            seen[conv.content_hash] = conv.id
    return duplicates


def mark_tombstone(
    db_conn: sqlite3.Connection,
    conversation_id: str,
    reason: str,
) -> None:
    """Mark a conversation as deleted/missing, preserving its data."""
    convs = list_conversations(db_conn)
    for conv in convs:
        if conv.id == conversation_id:
            conv.deleted_or_missing = True
            conv.status = ConversationStatus.DELETED
            conv.error_note = reason
            upsert_conversation(db_conn, conv)
            logger.info("Tombstoned conversation %s: %s", conversation_id, reason)
            return
    logger.warning("Conversation %s not found for tombstone.", conversation_id)


def snapshot_if_changed(
    db_conn: sqlite3.Connection,
    conv: Conversation,
) -> ConversationSnapshot | None:
    """Create a ConversationSnapshot if this conversation's hash has changed.

    Returns the new snapshot, or None if nothing changed.
    """
    from ..db import get_conversation

    existing = get_conversation(db_conn, conv.provider.value, conv.provider_conversation_id)
    prior_hash = ""
    if existing:
        if existing.content_hash == conv.content_hash:
            return None
        prior_hash = existing.content_hash or ""

    snap_id = uuid.uuid4().hex[:16]
    now = utcnow()
    snap = ConversationSnapshot(
        snapshot_id=snap_id,
        conversation_id=conv.id or conv.provider_conversation_id,
        provider=conv.provider,
        provider_conversation_id=conv.provider_conversation_id,
        captured_at=now,
        content_hash=conv.content_hash,
        prior_hash=prior_hash,
        first_seen_at=existing.extracted_at if existing and existing.extracted_at else now,
        last_seen_at=now,
        status=conv.status,
        raw_html_path=conv.raw_html_path,
        normalized_json_path=conv.normalized_json_path,
        markdown_path=conv.markdown_path,
    )
    upsert_snapshot(db_conn, snap)
    logger.debug(
        "Snapshot created for %s (hash changed: %s -> %s)",
        conv.provider_conversation_id,
        prior_hash[:8],
        conv.content_hash[:8],
    )
    return snap
