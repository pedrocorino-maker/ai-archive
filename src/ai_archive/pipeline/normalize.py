"""AI Archive — NormalizePipeline: clean and serialize conversation artifacts."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ..db import list_conversations, upsert_conversation
from ..logging_config import get_logger
from ..models import Conversation, Provider
from ..utils.files import ensure_dir, make_conversation_raw_path, safe_write, write_json
from ..utils.markdown import conversation_to_markdown
from ..utils.text import normalize_whitespace, truncate
from ..utils.time import utcnow

logger = get_logger("pipeline.normalize")

_CANONICAL_MAX_CHARS = 4000


def normalize_conversation(
    conv: Conversation,
    normalized_dir: Path,
) -> Conversation:
    """Normalize all message texts, compute canonical_text and content_hash.

    Saves normalized JSON and Markdown artifacts to disk.
    Returns the updated Conversation.
    """
    now = utcnow()
    year, month = now.year, now.month
    if conv.extracted_at:
        year = conv.extracted_at.year
        month = conv.extracted_at.month

    # Normalize each message
    canonical_parts: list[str] = []
    for msg in conv.messages:
        normalized = normalize_whitespace(msg.raw_text)
        msg.normalized_text = normalized
        msg.content_hash = msg.compute_hash()
        canonical_parts.append(f"[{msg.role.value}] {normalized}")

    # Canonical text for embeddings (truncated)
    full_canonical = "\n\n".join(canonical_parts)
    conv.canonical_text = truncate(full_canonical, _CANONICAL_MAX_CHARS)
    conv.content_hash = conv.compute_hash()
    conv.message_count = len(conv.messages)

    # Paths
    base = normalized_dir / conv.provider.value / f"{year:04d}" / f"{month:02d}"
    ensure_dir(base)

    json_path = base / f"{conv.provider_conversation_id}.json"
    md_path = base / f"{conv.provider_conversation_id}.md"

    # Write normalized JSON
    write_json(json_path, conv.model_dump(mode="json"))
    conv.normalized_json_path = str(json_path)

    # Write Markdown
    md_content = conversation_to_markdown(conv)
    safe_write(md_path, md_content)
    conv.markdown_path = str(md_path)

    return conv


def normalize_all(
    db_conn: sqlite3.Connection,
    normalized_dir: Path,
    provider: str | None = None,
) -> int:
    """Normalize all conversations in the DB (optionally filtered by provider).

    Returns the count of conversations processed.
    """
    convs = list_conversations(db_conn, provider=provider)
    count = 0
    for conv in convs:
        try:
            updated = normalize_conversation(conv, normalized_dir)
            upsert_conversation(db_conn, updated)
            count += 1
        except Exception as exc:
            logger.warning("Failed to normalize conversation %s: %s", conv.id, exc)
    logger.info("Normalized %d conversations (provider=%s)", count, provider or "all")
    return count
