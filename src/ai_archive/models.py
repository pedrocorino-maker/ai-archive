"""AI Archive — Pydantic v2 domain models."""
from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Provider(str, Enum):
    CHATGPT = "chatgpt"
    GEMINI = "gemini"


class AuthMode(str, Enum):
    ATTACH_CDP = "attach_cdp"
    MANAGED_PROFILE = "managed_profile"
    STORAGE_STATE_ONLY = "storage_state_only"


class ConversationStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"
    MISSING = "missing"
    INCOMPLETE = "incomplete"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class CodeBlock(BaseModel):
    language: str = ""
    code: str
    ordinal: int = 0


class Attachment(BaseModel):
    attachment_id: str = ""
    name: str = ""
    url: str = ""
    mime_type: str = ""
    size_bytes: int = 0
    local_path: str = ""


class Message(BaseModel):
    provider_message_id: str = ""
    role: MessageRole
    author: str = ""
    timestamp: datetime | None = None
    raw_text: str = ""
    normalized_text: str = ""
    code_blocks: list[CodeBlock] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    content_hash: str = ""
    ordinal: int = 0

    def compute_hash(self) -> str:
        data = f"{self.role}:{self.raw_text}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def model_post_init(self, __context: Any) -> None:
        if not self.content_hash:
            self.content_hash = self.compute_hash()


class Conversation(BaseModel):
    id: str = ""
    provider: Provider
    provider_conversation_id: str
    title: str = ""
    url: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    extracted_at: datetime = Field(default_factory=datetime.utcnow)
    model_name: str = ""
    message_count: int = 0
    content_hash: str = ""
    canonical_text: str = ""
    raw_html_path: str = ""
    normalized_json_path: str = ""
    markdown_path: str = ""
    deleted_or_missing: bool = False
    tags: list[str] = Field(default_factory=list)
    primary_topic_id: str = ""
    primary_topic_title: str = ""
    primary_topic_slug: str = ""
    messages: list[Message] = Field(default_factory=list)
    status: ConversationStatus = ConversationStatus.ACTIVE
    error_note: str = ""

    def compute_hash(self) -> str:
        texts = [m.content_hash for m in self.messages]
        combined = "|".join(texts)
        return hashlib.sha256(combined.encode()).hexdigest()[:20]


class ProviderAccount(BaseModel):
    provider: Provider
    display_name: str = ""
    email: str = ""
    auth_mode: AuthMode = AuthMode.ATTACH_CDP
    storage_state_path: str = ""
    last_authenticated_at: datetime | None = None


class CrawlRun(BaseModel):
    run_id: str
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None
    provider: Provider | None = None
    auth_mode: AuthMode = AuthMode.ATTACH_CDP
    conversations_found: int = 0
    conversations_new: int = 0
    conversations_updated: int = 0
    conversations_failed: int = 0
    topics_consolidated: int = 0
    drive_uploads: int = 0
    success: bool = False
    error_summary: str = ""
    # Backfill-only fields (not persisted to DB)
    harvest_discovered: int = 0
    harvest_duration_minutes: float = 0.0
    harvest_end_reason: str = ""


class CrawlError(BaseModel):
    run_id: str
    provider: Provider
    conversation_id: str = ""
    conversation_url: str = ""
    error_type: str = ""
    message: str = ""
    traceback: str = ""
    screenshot_path: str = ""
    html_path: str = ""
    occurred_at: datetime = Field(default_factory=datetime.utcnow)


class ConversationSnapshot(BaseModel):
    snapshot_id: str
    conversation_id: str
    provider: Provider
    provider_conversation_id: str
    captured_at: datetime = Field(default_factory=datetime.utcnow)
    content_hash: str
    prior_hash: str = ""
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)
    status: ConversationStatus = ConversationStatus.ACTIVE
    raw_html_path: str = ""
    normalized_json_path: str = ""
    markdown_path: str = ""
    drive_file_ids: dict[str, str] = Field(default_factory=dict)


class TopicCluster(BaseModel):
    topic_id: str
    topic_title: str
    topic_slug: str
    tags: list[str] = Field(default_factory=list)
    conversation_ids: list[str] = Field(default_factory=list)
    provider_counts: dict[str, int] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    centroid_embedding: list[float] = Field(default_factory=list)


class CanonicalTopicDoc(BaseModel):
    topic_id: str
    canonical_title: str
    slug: str
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    providers: list[str] = Field(default_factory=list)
    conversation_count: int = 0
    source_refs: list[dict[str, str]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    markdown_path: str = ""
    manifest_path: str = ""
    drive_file_id: str = ""


class DriveSyncEntry(BaseModel):
    local_path: str
    drive_file_id: str
    drive_parent_id: str
    mime_type: str = "text/plain"
    last_synced_at: datetime = Field(default_factory=datetime.utcnow)
    content_hash: str = ""
    sync_status: str = "synced"


class SelectorProfile(BaseModel):
    provider: Provider
    version: str = "1.0"
    selectors: dict[str, list[str]] = Field(default_factory=dict)


class AuthStateInfo(BaseModel):
    provider: Provider
    auth_mode: AuthMode
    is_authenticated: bool = False
    has_challenge: bool = False
    challenge_type: str = ""
    storage_state_path: str = ""
    last_checked_at: datetime = Field(default_factory=datetime.utcnow)
    notes: str = ""
