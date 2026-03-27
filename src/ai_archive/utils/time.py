"""AI Archive — time/date utilities."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


_FORMATS = [
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
]


def utcnow() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(tz=timezone.utc)


def parse_timestamp(text: str) -> Optional[datetime]:
    """Attempt to parse a timestamp string using multiple formats."""
    if not text:
        return None
    text = text.strip()
    # Try ISO format via fromisoformat first (handles most modern cases)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in _FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def format_iso(dt: datetime) -> str:
    """Format a datetime as ISO 8601 string."""
    return dt.isoformat()


def month_folder(dt: datetime) -> str:
    """Return 'YYYY/MM' string for use in directory paths."""
    return f"{dt.year:04d}/{dt.month:02d}"
