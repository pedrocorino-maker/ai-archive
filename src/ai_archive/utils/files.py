"""AI Archive — file system utilities."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Union

import orjson


def ensure_dir(path: Path) -> Path:
    """Create directory (and parents) if it doesn't exist, return path."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_write(path: Path, content: Union[str, bytes], mode: str = "w") -> None:
    """Write content to path, creating parent directories as needed."""
    ensure_dir(path.parent)
    if isinstance(content, bytes):
        with open(path, "wb") as f:
            f.write(content)
    else:
        with open(path, mode, encoding="utf-8") as f:
            f.write(content)


def atomic_write(path: Path, content: Union[str, bytes]) -> None:
    """Write content atomically: write to .tmp then rename."""
    ensure_dir(path.parent)
    suffix = ".tmp"
    tmp_path = path.with_suffix(path.suffix + suffix)
    try:
        if isinstance(content, bytes):
            with open(tmp_path, "wb") as f:
                f.write(content)
        else:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def read_json(path: Path) -> dict:
    """Read a JSON file and return parsed dict."""
    with open(path, "rb") as f:
        return orjson.loads(f.read())


def write_json(path: Path, data: Union[dict, list]) -> None:
    """Write data as JSON using orjson (fast, handles datetime)."""
    ensure_dir(path.parent)
    content = orjson.dumps(
        data,
        option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY,
    )
    atomic_write(path, content)


def write_jsonl(path: Path, records: list[dict], append: bool = False) -> None:
    """Write a list of dicts as JSON Lines."""
    ensure_dir(path.parent)
    mode = "ab" if append else "wb"
    with open(path, mode) as f:
        for record in records:
            f.write(orjson.dumps(record) + b"\n")


def sanitize_filename(name: str) -> str:
    """Remove or replace characters unsafe for filenames."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"_{2,}", "_", name)
    name = name.strip(". _")
    return name[:200] if name else "unnamed"


def make_conversation_raw_path(
    base: Path,
    provider: str,
    year: int,
    month: int,
    conv_id: str,
) -> Path:
    """Return the standard raw HTML path for a conversation."""
    return base / provider / f"{year:04d}" / f"{month:02d}" / f"{sanitize_filename(conv_id)}.html"


def make_topic_curated_path(base: Path, slug: str) -> Path:
    """Return the standard curated markdown path for a topic."""
    safe_slug = sanitize_filename(slug)
    return base / "topics" / safe_slug / f"{safe_slug}.md"
