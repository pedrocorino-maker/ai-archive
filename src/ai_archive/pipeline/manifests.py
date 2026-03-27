"""AI Archive — Manifest helpers for conversation artifacts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..models import Conversation
from ..utils.files import ensure_dir, read_json, write_json
from ..utils.time import utcnow


class ManifestEntry(BaseModel):
    """Manifest metadata stored alongside each raw conversation artifact."""

    conversation_id: str
    provider: str
    provider_conversation_id: str
    title: str = ""
    url: str = ""
    extracted_at: str = ""
    content_hash: str = ""
    message_count: int = 0
    raw_html_path: str = ""
    normalized_json_path: str = ""
    markdown_path: str = ""
    model_name: str = ""
    tags: list[str] = []
    primary_topic_id: str = ""
    primary_topic_slug: str = ""
    manifest_version: str = "1.0"
    generated_at: str = ""


def build_manifest(conv: Conversation) -> dict[str, Any]:
    """Build a manifest dict from a Conversation."""
    return {
        "conversation_id": conv.id,
        "provider": conv.provider.value,
        "provider_conversation_id": conv.provider_conversation_id,
        "title": conv.title,
        "url": conv.url,
        "extracted_at": conv.extracted_at.isoformat() if conv.extracted_at else "",
        "content_hash": conv.content_hash,
        "message_count": len(conv.messages),
        "raw_html_path": conv.raw_html_path,
        "normalized_json_path": conv.normalized_json_path,
        "markdown_path": conv.markdown_path,
        "model_name": conv.model_name,
        "tags": conv.tags,
        "primary_topic_id": conv.primary_topic_id,
        "primary_topic_slug": conv.primary_topic_slug,
        "manifest_version": "1.0",
        "generated_at": utcnow().isoformat(),
    }


def write_manifest(conv: Conversation, base_dir: Path) -> Path:
    """Write a manifest JSON file next to the raw HTML file.

    The manifest is named <provider_conversation_id>__manifest.json
    in the same directory as the raw HTML.

    Returns the manifest path.
    """
    manifest_data = build_manifest(conv)

    if conv.raw_html_path:
        raw_dir = Path(conv.raw_html_path).parent
    else:
        raw_dir = base_dir

    ensure_dir(raw_dir)
    manifest_path = raw_dir / f"{conv.provider_conversation_id}__manifest.json"
    write_json(manifest_path, manifest_data)
    return manifest_path


def read_manifest(path: Path) -> dict[str, Any]:
    """Read and return a manifest JSON file as a dict."""
    return read_json(path)


def compute_manifest_hash(manifest: dict[str, Any]) -> str:
    """Compute a deterministic hash of the manifest content.

    Excludes 'generated_at' for stability.
    """
    stable = {k: v for k, v in manifest.items() if k != "generated_at"}
    serialized = json.dumps(stable, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:20]
