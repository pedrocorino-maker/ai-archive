"""AI Archive — DriveMirror: mirror a local directory tree to Google Drive."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from ..db import get_drive_entry, upsert_drive_entry
from ..logging_config import get_logger
from ..models import DriveSyncEntry
from ..utils.hashing import sha256_file
from ..utils.time import utcnow

logger = get_logger("drive.mirror")

# Local cache file for folder_id mappings to avoid repeated API calls
_CACHE_FILENAME = ".drive_folder_cache.json"


class DriveMirror:
    """Mirrors a local directory tree to a Google Drive folder."""

    def __init__(self, drive_api: object) -> None:
        self._drive = drive_api  # DriveAPI instance
        self._folder_cache: dict[str, str] = {}  # local_rel_path -> drive_folder_id

    def _load_cache(self, local_base: Path) -> None:
        """Load folder ID cache from a local JSON file."""
        cache_path = local_base / _CACHE_FILENAME
        if cache_path.exists():
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                self._folder_cache = data
            except Exception as exc:
                logger.debug("Could not load folder cache: %s", exc)
                self._folder_cache = {}

    def _save_cache(self, local_base: Path) -> None:
        """Persist the folder ID cache to disk."""
        cache_path = local_base / _CACHE_FILENAME
        try:
            cache_path.write_text(
                json.dumps(self._folder_cache, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.debug("Could not save folder cache: %s", exc)

    def _get_or_create_folder(
        self,
        local_base: Path,
        drive_root_id: str,
        relative_dir: Path,
    ) -> str:
        """Recursively ensure the Drive folder path exists, using cache.

        Returns the drive_folder_id for the deepest folder.
        """
        parts = relative_dir.parts
        if not parts:
            return drive_root_id

        # Build path incrementally
        current_drive_id = drive_root_id
        accumulated = ""
        for part in parts:
            accumulated = f"{accumulated}/{part}" if accumulated else part
            if accumulated in self._folder_cache:
                current_drive_id = self._folder_cache[accumulated]
            else:
                current_drive_id = self._drive.get_or_create_folder(  # type: ignore[attr-defined]
                    current_drive_id, part
                )
                self._folder_cache[accumulated] = current_drive_id

        return current_drive_id

    def mirror_tree(
        self,
        local_base: Path,
        drive_root_id: str,
        db_conn: sqlite3.Connection,
        extensions: Optional[set[str]] = None,
    ) -> dict:
        """Recursively mirror local_base to the Drive folder at drive_root_id.

        Args:
            local_base: local directory to mirror
            drive_root_id: Drive folder ID for the root
            db_conn: sqlite3 connection for tracking sync entries
            extensions: if set, only files with these extensions are synced

        Returns:
            stats dict: files_created, files_updated, files_skipped
        """
        if not local_base.exists():
            logger.warning("Local base directory does not exist: %s", local_base)
            return {"files_created": 0, "files_updated": 0, "files_skipped": 0}

        self._load_cache(local_base)
        stats = {"files_created": 0, "files_updated": 0, "files_skipped": 0}

        for local_path in local_base.rglob("*"):
            if not local_path.is_file():
                continue
            if local_path.name.startswith("."):
                continue
            if extensions and local_path.suffix.lower() not in extensions:
                continue

            relative = local_path.relative_to(local_base)
            parent_rel = relative.parent

            try:
                drive_folder_id = self._get_or_create_folder(
                    local_base, drive_root_id, parent_rel
                )
            except Exception as exc:
                logger.warning("Failed to get/create folder for %s: %s", parent_rel, exc)
                continue

            try:
                entry = self._sync_one_file(
                    local_path, drive_folder_id, db_conn
                )
                status = entry.sync_status
                if status == "created":
                    stats["files_created"] += 1
                elif status == "updated":
                    stats["files_updated"] += 1
                else:
                    stats["files_skipped"] += 1
            except Exception as exc:
                logger.warning("Failed to sync %s: %s", local_path, exc)

        self._save_cache(local_base)
        logger.info(
            "Mirror complete for %s: %s",
            local_base,
            stats,
        )
        return stats

    def _sync_one_file(
        self,
        local_path: Path,
        drive_parent_id: str,
        db_conn: sqlite3.Connection,
    ) -> DriveSyncEntry:
        """Sync a single file. Creates or updates on Drive. Returns DriveSyncEntry."""
        file_hash = sha256_file(local_path)
        existing = get_drive_entry(db_conn, str(local_path))

        if existing and existing.content_hash == file_hash:
            existing.sync_status = "skipped"
            return existing

        from ..drive.api import _detect_mime
        mime_type = _detect_mime(local_path)
        file_name = local_path.name

        if existing and existing.drive_file_id:
            file_id = self._drive.update_file(existing.drive_file_id, local_path)  # type: ignore[attr-defined]
            sync_status = "updated"
        else:
            existing_id = self._drive.file_exists(drive_parent_id, file_name)  # type: ignore[attr-defined]
            if existing_id:
                file_id = self._drive.update_file(existing_id, local_path)  # type: ignore[attr-defined]
                sync_status = "updated"
            else:
                file_id = self._drive.upload_file(local_path, drive_parent_id, mime_type=mime_type)  # type: ignore[attr-defined]
                sync_status = "created"

        entry = DriveSyncEntry(
            local_path=str(local_path),
            drive_file_id=file_id,
            drive_parent_id=drive_parent_id,
            mime_type=mime_type,
            last_synced_at=utcnow(),
            content_hash=file_hash,
            sync_status=sync_status,
        )
        upsert_drive_entry(db_conn, entry)
        return entry
