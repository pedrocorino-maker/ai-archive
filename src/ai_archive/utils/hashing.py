"""AI Archive — hashing utilities."""
from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def short_hash(text: str, length: int = 8) -> str:
    return sha256_text(text)[:length]


def content_fingerprint(texts: list[str]) -> str:
    combined = "|".join(sorted(texts))
    return sha256_text(combined)[:20]


def message_hash(role: str, text: str) -> str:
    return short_hash(f"{role}:{text}", 16)


def conversation_hash(message_hashes: list[str]) -> str:
    return content_fingerprint(message_hashes)
