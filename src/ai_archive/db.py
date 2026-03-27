"""AI Archive — SQLite database layer."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator

from .models import (
    CanonicalTopicDoc,
    Conversation,
    ConversationSnapshot,
    ConversationStatus,
    CrawlError,
    CrawlRun,
    DriveSyncEntry,
    Message,
    MessageRole,
    Provider,
    TopicCluster,
    AuthMode,
    CodeBlock,
)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_conversation_id TEXT NOT NULL,
    title TEXT DEFAULT '',
    url TEXT DEFAULT '',
    created_at TEXT,
    updated_at TEXT,
    extracted_at TEXT,
    model_name TEXT DEFAULT '',
    message_count INTEGER DEFAULT 0,
    content_hash TEXT DEFAULT '',
    canonical_text TEXT DEFAULT '',
    raw_html_path TEXT DEFAULT '',
    normalized_json_path TEXT DEFAULT '',
    markdown_path TEXT DEFAULT '',
    deleted_or_missing INTEGER DEFAULT 0,
    tags TEXT DEFAULT '[]',
    primary_topic_id TEXT DEFAULT '',
    primary_topic_title TEXT DEFAULT '',
    primary_topic_slug TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    error_note TEXT DEFAULT '',
    UNIQUE(provider, provider_conversation_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    provider_message_id TEXT DEFAULT '',
    role TEXT NOT NULL,
    author TEXT DEFAULT '',
    timestamp TEXT,
    raw_text TEXT DEFAULT '',
    normalized_text TEXT DEFAULT '',
    code_blocks TEXT DEFAULT '[]',
    attachments TEXT DEFAULT '[]',
    content_hash TEXT DEFAULT '',
    ordinal INTEGER DEFAULT 0,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE TABLE IF NOT EXISTS conversation_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_conversation_id TEXT NOT NULL,
    captured_at TEXT,
    content_hash TEXT NOT NULL,
    prior_hash TEXT DEFAULT '',
    first_seen_at TEXT,
    last_seen_at TEXT,
    status TEXT DEFAULT 'active',
    raw_html_path TEXT DEFAULT '',
    normalized_json_path TEXT DEFAULT '',
    markdown_path TEXT DEFAULT '',
    drive_file_ids TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS topic_clusters (
    topic_id TEXT PRIMARY KEY,
    topic_title TEXT NOT NULL,
    topic_slug TEXT NOT NULL,
    tags TEXT DEFAULT '[]',
    conversation_ids TEXT DEFAULT '[]',
    provider_counts TEXT DEFAULT '{}',
    created_at TEXT,
    updated_at TEXT,
    centroid_embedding TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS canonical_topic_docs (
    topic_id TEXT PRIMARY KEY,
    canonical_title TEXT NOT NULL,
    slug TEXT NOT NULL,
    updated_at TEXT,
    providers TEXT DEFAULT '[]',
    conversation_count INTEGER DEFAULT 0,
    source_refs TEXT DEFAULT '[]',
    tags TEXT DEFAULT '[]',
    markdown_path TEXT DEFAULT '',
    manifest_path TEXT DEFAULT '',
    drive_file_id TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS drive_sync_entries (
    local_path TEXT PRIMARY KEY,
    drive_file_id TEXT NOT NULL,
    drive_parent_id TEXT NOT NULL,
    mime_type TEXT DEFAULT 'text/plain',
    last_synced_at TEXT,
    content_hash TEXT DEFAULT '',
    sync_status TEXT DEFAULT 'synced'
);

CREATE TABLE IF NOT EXISTS crawl_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT,
    finished_at TEXT,
    provider TEXT,
    auth_mode TEXT DEFAULT 'attach_cdp',
    conversations_found INTEGER DEFAULT 0,
    conversations_new INTEGER DEFAULT 0,
    conversations_updated INTEGER DEFAULT 0,
    conversations_failed INTEGER DEFAULT 0,
    topics_consolidated INTEGER DEFAULT 0,
    drive_uploads INTEGER DEFAULT 0,
    success INTEGER DEFAULT 0,
    error_summary TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS crawl_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    conversation_id TEXT DEFAULT '',
    conversation_url TEXT DEFAULT '',
    error_type TEXT DEFAULT '',
    message TEXT DEFAULT '',
    traceback TEXT DEFAULT '',
    screenshot_path TEXT DEFAULT '',
    html_path TEXT DEFAULT '',
    occurred_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_conversations_provider ON conversations(provider);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_conversation ON conversation_snapshots(conversation_id);
CREATE INDEX IF NOT EXISTS idx_errors_run ON crawl_errors(run_id);
"""


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def init_db(db_path: Path | str) -> sqlite3.Connection:
    """Create all tables and return an open connection."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


@contextmanager
def get_db_connection(db_path: Path | str) -> Generator[sqlite3.Connection, None, None]:
    conn = init_db(db_path)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt(val: datetime | None) -> str | None:
    if val is None:
        return None
    return val.isoformat()


def _parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    return datetime.fromisoformat(val)


def _j(val: object) -> str:
    return json.dumps(val)


def _jl(val: str | None, default: list) -> list:
    if not val:
        return default
    try:
        return json.loads(val)
    except Exception:
        return default


def _jd(val: str | None, default: dict) -> dict:
    if not val:
        return default
    try:
        return json.loads(val)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def upsert_conversation(conn: sqlite3.Connection, conv: Conversation) -> None:
    messages_data = [m.model_dump(mode="json") for m in conv.messages]
    conn.execute(
        """
        INSERT INTO conversations (
            id, provider, provider_conversation_id, title, url,
            created_at, updated_at, extracted_at, model_name, message_count,
            content_hash, canonical_text, raw_html_path, normalized_json_path,
            markdown_path, deleted_or_missing, tags, primary_topic_id,
            primary_topic_title, primary_topic_slug, status, error_note
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(provider, provider_conversation_id) DO UPDATE SET
            title=excluded.title,
            url=excluded.url,
            updated_at=excluded.updated_at,
            extracted_at=excluded.extracted_at,
            model_name=excluded.model_name,
            message_count=excluded.message_count,
            content_hash=excluded.content_hash,
            canonical_text=excluded.canonical_text,
            raw_html_path=excluded.raw_html_path,
            normalized_json_path=excluded.normalized_json_path,
            markdown_path=excluded.markdown_path,
            deleted_or_missing=excluded.deleted_or_missing,
            tags=excluded.tags,
            primary_topic_id=excluded.primary_topic_id,
            primary_topic_title=excluded.primary_topic_title,
            primary_topic_slug=excluded.primary_topic_slug,
            status=excluded.status,
            error_note=excluded.error_note
        """,
        (
            conv.id,
            conv.provider.value,
            conv.provider_conversation_id,
            conv.title,
            conv.url,
            _dt(conv.created_at),
            _dt(conv.updated_at),
            _dt(conv.extracted_at),
            conv.model_name,
            conv.message_count,
            conv.content_hash,
            conv.canonical_text,
            conv.raw_html_path,
            conv.normalized_json_path,
            conv.markdown_path,
            int(conv.deleted_or_missing),
            _j(conv.tags),
            conv.primary_topic_id,
            conv.primary_topic_title,
            conv.primary_topic_slug,
            conv.status.value,
            conv.error_note,
        ),
    )
    # After upsert, retrieve the actual id in the DB.
    # ON CONFLICT DO UPDATE keeps the original id, so conv.id may differ.
    id_row = conn.execute(
        "SELECT id FROM conversations WHERE provider=? AND provider_conversation_id=?",
        (conv.provider.value, conv.provider_conversation_id),
    ).fetchone()
    actual_id = id_row["id"] if id_row else conv.id

    # Upsert messages using the stable actual_id
    conn.execute("DELETE FROM messages WHERE conversation_id=?", (actual_id,))
    for msg in conv.messages:
        conn.execute(
            """
            INSERT INTO messages (
                conversation_id, provider_message_id, role, author, timestamp,
                raw_text, normalized_text, code_blocks, attachments,
                content_hash, ordinal
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                actual_id,
                msg.provider_message_id,
                msg.role.value,
                msg.author,
                _dt(msg.timestamp),
                msg.raw_text,
                msg.normalized_text,
                _j([cb.model_dump() for cb in msg.code_blocks]),
                _j([a.model_dump() for a in msg.attachments]),
                msg.content_hash,
                msg.ordinal,
            ),
        )
    conn.commit()


def _row_to_conversation(row: sqlite3.Row, messages: list[Message]) -> Conversation:
    return Conversation(
        id=row["id"],
        provider=Provider(row["provider"]),
        provider_conversation_id=row["provider_conversation_id"],
        title=row["title"] or "",
        url=row["url"] or "",
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
        extracted_at=_parse_dt(row["extracted_at"]) or datetime.utcnow(),
        model_name=row["model_name"] or "",
        message_count=row["message_count"] or 0,
        content_hash=row["content_hash"] or "",
        canonical_text=row["canonical_text"] or "",
        raw_html_path=row["raw_html_path"] or "",
        normalized_json_path=row["normalized_json_path"] or "",
        markdown_path=row["markdown_path"] or "",
        deleted_or_missing=bool(row["deleted_or_missing"]),
        tags=_jl(row["tags"], []),
        primary_topic_id=row["primary_topic_id"] or "",
        primary_topic_title=row["primary_topic_title"] or "",
        primary_topic_slug=row["primary_topic_slug"] or "",
        status=ConversationStatus(row["status"] or "active"),
        error_note=row["error_note"] or "",
        messages=messages,
    )


def _load_messages_for(conn: sqlite3.Connection, conversation_id: str) -> list[Message]:
    rows = conn.execute(
        "SELECT * FROM messages WHERE conversation_id=? ORDER BY ordinal ASC",
        (conversation_id,),
    ).fetchall()
    messages = []
    for r in rows:
        code_blocks_data = _jl(r["code_blocks"], [])
        code_blocks = [CodeBlock(**cb) for cb in code_blocks_data]
        messages.append(
            Message(
                provider_message_id=r["provider_message_id"] or "",
                role=MessageRole(r["role"]),
                author=r["author"] or "",
                timestamp=_parse_dt(r["timestamp"]),
                raw_text=r["raw_text"] or "",
                normalized_text=r["normalized_text"] or "",
                code_blocks=code_blocks,
                content_hash=r["content_hash"] or "",
                ordinal=r["ordinal"] or 0,
            )
        )
    return messages


def get_conversation(
    conn: sqlite3.Connection, provider: str, provider_conversation_id: str
) -> Conversation | None:
    row = conn.execute(
        "SELECT * FROM conversations WHERE provider=? AND provider_conversation_id=?",
        (provider, provider_conversation_id),
    ).fetchone()
    if row is None:
        return None
    messages = _load_messages_for(conn, row["id"])
    return _row_to_conversation(row, messages)


def list_conversations(
    conn: sqlite3.Connection,
    provider: str | None = None,
    limit: int | None = None,
) -> list[Conversation]:
    if provider:
        rows = conn.execute(
            "SELECT * FROM conversations WHERE provider=? ORDER BY extracted_at DESC"
            + (f" LIMIT {int(limit)}" if limit else ""),
            (provider,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM conversations ORDER BY extracted_at DESC"
            + (f" LIMIT {int(limit)}" if limit else "")
        ).fetchall()
    result = []
    for row in rows:
        messages = _load_messages_for(conn, row["id"])
        result.append(_row_to_conversation(row, messages))
    return result


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

def upsert_snapshot(conn: sqlite3.Connection, snap: ConversationSnapshot) -> None:
    conn.execute(
        """
        INSERT INTO conversation_snapshots (
            snapshot_id, conversation_id, provider, provider_conversation_id,
            captured_at, content_hash, prior_hash, first_seen_at, last_seen_at,
            status, raw_html_path, normalized_json_path, markdown_path, drive_file_ids
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(snapshot_id) DO UPDATE SET
            last_seen_at=excluded.last_seen_at,
            status=excluded.status,
            drive_file_ids=excluded.drive_file_ids
        """,
        (
            snap.snapshot_id,
            snap.conversation_id,
            snap.provider.value,
            snap.provider_conversation_id,
            _dt(snap.captured_at),
            snap.content_hash,
            snap.prior_hash,
            _dt(snap.first_seen_at),
            _dt(snap.last_seen_at),
            snap.status.value,
            snap.raw_html_path,
            snap.normalized_json_path,
            snap.markdown_path,
            _j(snap.drive_file_ids),
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------

def clear_topics_and_docs(conn: sqlite3.Connection) -> None:
    """Delete all topic_clusters and canonical_topic_docs rows (full rebuild)."""
    conn.execute("DELETE FROM topic_clusters")
    conn.execute("DELETE FROM canonical_topic_docs")
    conn.commit()


def upsert_topic(conn: sqlite3.Connection, topic: TopicCluster) -> None:
    conn.execute(
        """
        INSERT INTO topic_clusters (
            topic_id, topic_title, topic_slug, tags, conversation_ids,
            provider_counts, created_at, updated_at, centroid_embedding
        ) VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(topic_id) DO UPDATE SET
            topic_title=excluded.topic_title,
            topic_slug=excluded.topic_slug,
            tags=excluded.tags,
            conversation_ids=excluded.conversation_ids,
            provider_counts=excluded.provider_counts,
            updated_at=excluded.updated_at,
            centroid_embedding=excluded.centroid_embedding
        """,
        (
            topic.topic_id,
            topic.topic_title,
            topic.topic_slug,
            _j(topic.tags),
            _j(topic.conversation_ids),
            _j(topic.provider_counts),
            _dt(topic.created_at),
            _dt(topic.updated_at),
            _j(topic.centroid_embedding),
        ),
    )
    conn.commit()


def get_topic(conn: sqlite3.Connection, topic_id: str) -> TopicCluster | None:
    row = conn.execute(
        "SELECT * FROM topic_clusters WHERE topic_id=?", (topic_id,)
    ).fetchone()
    if row is None:
        return None
    return TopicCluster(
        topic_id=row["topic_id"],
        topic_title=row["topic_title"],
        topic_slug=row["topic_slug"],
        tags=_jl(row["tags"], []),
        conversation_ids=_jl(row["conversation_ids"], []),
        provider_counts=_jd(row["provider_counts"], {}),
        created_at=_parse_dt(row["created_at"]) or datetime.utcnow(),
        updated_at=_parse_dt(row["updated_at"]) or datetime.utcnow(),
        centroid_embedding=_jl(row["centroid_embedding"], []),
    )


def list_topics(conn: sqlite3.Connection) -> list[TopicCluster]:
    rows = conn.execute("SELECT * FROM topic_clusters ORDER BY topic_title").fetchall()
    result = []
    for row in rows:
        result.append(
            TopicCluster(
                topic_id=row["topic_id"],
                topic_title=row["topic_title"],
                topic_slug=row["topic_slug"],
                tags=_jl(row["tags"], []),
                conversation_ids=_jl(row["conversation_ids"], []),
                provider_counts=_jd(row["provider_counts"], {}),
                created_at=_parse_dt(row["created_at"]) or datetime.utcnow(),
                updated_at=_parse_dt(row["updated_at"]) or datetime.utcnow(),
                centroid_embedding=_jl(row["centroid_embedding"], []),
            )
        )
    return result


# ---------------------------------------------------------------------------
# Canonical docs
# ---------------------------------------------------------------------------

def upsert_canonical_doc(conn: sqlite3.Connection, doc: CanonicalTopicDoc) -> None:
    conn.execute(
        """
        INSERT INTO canonical_topic_docs (
            topic_id, canonical_title, slug, updated_at, providers,
            conversation_count, source_refs, tags, markdown_path,
            manifest_path, drive_file_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(topic_id) DO UPDATE SET
            canonical_title=excluded.canonical_title,
            slug=excluded.slug,
            updated_at=excluded.updated_at,
            providers=excluded.providers,
            conversation_count=excluded.conversation_count,
            source_refs=excluded.source_refs,
            tags=excluded.tags,
            markdown_path=excluded.markdown_path,
            manifest_path=excluded.manifest_path,
            drive_file_id=excluded.drive_file_id
        """,
        (
            doc.topic_id,
            doc.canonical_title,
            doc.slug,
            _dt(doc.updated_at),
            _j(doc.providers),
            doc.conversation_count,
            _j(doc.source_refs),
            _j(doc.tags),
            doc.markdown_path,
            doc.manifest_path,
            doc.drive_file_id,
        ),
    )
    conn.commit()


def list_canonical_docs(conn: sqlite3.Connection) -> list[CanonicalTopicDoc]:
    rows = conn.execute(
        "SELECT * FROM canonical_topic_docs ORDER BY canonical_title"
    ).fetchall()
    result = []
    for row in rows:
        result.append(
            CanonicalTopicDoc(
                topic_id=row["topic_id"],
                canonical_title=row["canonical_title"],
                slug=row["slug"],
                updated_at=_parse_dt(row["updated_at"]) or datetime.utcnow(),
                providers=_jl(row["providers"], []),
                conversation_count=row["conversation_count"] or 0,
                source_refs=_jl(row["source_refs"], []),
                tags=_jl(row["tags"], []),
                markdown_path=row["markdown_path"] or "",
                manifest_path=row["manifest_path"] or "",
                drive_file_id=row["drive_file_id"] or "",
            )
        )
    return result


# ---------------------------------------------------------------------------
# Drive sync entries
# ---------------------------------------------------------------------------

def upsert_drive_entry(conn: sqlite3.Connection, entry: DriveSyncEntry) -> None:
    conn.execute(
        """
        INSERT INTO drive_sync_entries (
            local_path, drive_file_id, drive_parent_id, mime_type,
            last_synced_at, content_hash, sync_status
        ) VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(local_path) DO UPDATE SET
            drive_file_id=excluded.drive_file_id,
            drive_parent_id=excluded.drive_parent_id,
            mime_type=excluded.mime_type,
            last_synced_at=excluded.last_synced_at,
            content_hash=excluded.content_hash,
            sync_status=excluded.sync_status
        """,
        (
            entry.local_path,
            entry.drive_file_id,
            entry.drive_parent_id,
            entry.mime_type,
            _dt(entry.last_synced_at),
            entry.content_hash,
            entry.sync_status,
        ),
    )
    conn.commit()


def get_drive_entry(conn: sqlite3.Connection, local_path: str) -> DriveSyncEntry | None:
    row = conn.execute(
        "SELECT * FROM drive_sync_entries WHERE local_path=?", (local_path,)
    ).fetchone()
    if row is None:
        return None
    return DriveSyncEntry(
        local_path=row["local_path"],
        drive_file_id=row["drive_file_id"],
        drive_parent_id=row["drive_parent_id"],
        mime_type=row["mime_type"] or "text/plain",
        last_synced_at=_parse_dt(row["last_synced_at"]) or datetime.utcnow(),
        content_hash=row["content_hash"] or "",
        sync_status=row["sync_status"] or "synced",
    )


# ---------------------------------------------------------------------------
# Crawl runs
# ---------------------------------------------------------------------------

def insert_crawl_run(conn: sqlite3.Connection, run: CrawlRun) -> None:
    conn.execute(
        """
        INSERT INTO crawl_runs (
            run_id, started_at, finished_at, provider, auth_mode,
            conversations_found, conversations_new, conversations_updated,
            conversations_failed, topics_consolidated, drive_uploads,
            success, error_summary
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run.run_id,
            _dt(run.started_at),
            _dt(run.finished_at),
            run.provider.value if run.provider else None,
            run.auth_mode.value,
            run.conversations_found,
            run.conversations_new,
            run.conversations_updated,
            run.conversations_failed,
            run.topics_consolidated,
            run.drive_uploads,
            int(run.success),
            run.error_summary,
        ),
    )
    conn.commit()


def update_crawl_run(conn: sqlite3.Connection, run: CrawlRun) -> None:
    conn.execute(
        """
        UPDATE crawl_runs SET
            finished_at=?, provider=?, auth_mode=?,
            conversations_found=?, conversations_new=?, conversations_updated=?,
            conversations_failed=?, topics_consolidated=?, drive_uploads=?,
            success=?, error_summary=?
        WHERE run_id=?
        """,
        (
            _dt(run.finished_at),
            run.provider.value if run.provider else None,
            run.auth_mode.value,
            run.conversations_found,
            run.conversations_new,
            run.conversations_updated,
            run.conversations_failed,
            run.topics_consolidated,
            run.drive_uploads,
            int(run.success),
            run.error_summary,
            run.run_id,
        ),
    )
    conn.commit()


def insert_crawl_error(conn: sqlite3.Connection, err: CrawlError) -> None:
    conn.execute(
        """
        INSERT INTO crawl_errors (
            run_id, provider, conversation_id, conversation_url,
            error_type, message, traceback, screenshot_path, html_path, occurred_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            err.run_id,
            err.provider.value,
            err.conversation_id,
            err.conversation_url,
            err.error_type,
            err.message,
            err.traceback,
            err.screenshot_path,
            err.html_path,
            _dt(err.occurred_at),
        ),
    )
    conn.commit()
