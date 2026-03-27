"""AI Archive — storage state management for Playwright contexts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..logging_config import get_logger

logger = get_logger("auth.storage_state")


async def save_storage_state(context: object, path: Path) -> None:
    """Save Playwright BrowserContext storage state to a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # context is playwright.async_api.BrowserContext
    state = await context.storage_state()  # type: ignore[attr-defined]
    # Inject a saved_at timestamp for freshness checks
    state["_saved_at"] = datetime.now(tz=timezone.utc).isoformat()
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    logger.info("Storage state saved to %s", path)


def load_storage_state_if_valid(path: Path, max_age_hours: int = 168) -> Optional[dict]:
    """Load storage state from disk if it exists and is not expired.

    Returns None if the file is missing, unreadable, or too old.
    """
    if not path.exists():
        return None
    if not is_state_fresh(path, max_age_hours=max_age_hours):
        logger.warning("Storage state at %s is stale, ignoring.", path)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception as exc:
        logger.warning("Failed to read storage state from %s: %s", path, exc)
        return None


def is_state_fresh(path: Path, max_age_hours: int = 168) -> bool:
    """Return True if the storage state file is younger than max_age_hours."""
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        saved_at_str = data.get("_saved_at")
        if not saved_at_str:
            # No timestamp embedded — fall back to file mtime
            import os
            mtime = os.path.getmtime(str(path))
            saved_at = datetime.fromtimestamp(mtime, tz=timezone.utc)
        else:
            saved_at = datetime.fromisoformat(saved_at_str)
            if saved_at.tzinfo is None:
                saved_at = saved_at.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(tz=timezone.utc) - saved_at).total_seconds() / 3600.0
        return age_hours < max_age_hours
    except Exception as exc:
        logger.debug("Could not determine storage state freshness: %s", exc)
        return False
