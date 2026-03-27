"""AI Archive — DriveSyncPipeline: sync local artifacts to Google Drive."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ..db import get_drive_entry, upsert_drive_entry
from ..logging_config import get_logger
from ..models import DriveSyncEntry
from ..utils.files import ensure_dir
from ..utils.hashing import sha256_file
from ..utils.time import utcnow

logger = get_logger("pipeline.drive_sync")


class DriveSyncPipeline:
    """Synchronizes local file trees to Google Drive."""

    def __init__(
        self,
        settings: object,
        db_conn: sqlite3.Connection,
        drive_api: object,
    ) -> None:
        self._settings = settings
        self._db = db_conn
        self._drive = drive_api  # DriveAPI instance

    def sync_file(
        self,
        local_path: Path,
        drive_parent_id: str,
        mime_type: str = "text/plain",
    ) -> DriveSyncEntry:
        """Upload or update a single file to Drive.

        Checks existing hash — skips upload if unchanged.
        Returns a DriveSyncEntry.
        """
        if not local_path.exists():
            raise FileNotFoundError(f"Local file not found: {local_path}")

        file_hash = sha256_file(local_path)
        existing = get_drive_entry(self._db, str(local_path))

        if existing and existing.content_hash == file_hash:
            logger.debug("Skipping unchanged file: %s", local_path.name)
            existing.sync_status = "skipped"
            return existing

        file_name = local_path.name

        if existing and existing.drive_file_id:
            # Update existing Drive file
            file_id = self._drive.update_file(existing.drive_file_id, local_path)
            sync_status = "updated"
            logger.info("Updated Drive file: %s -> %s", local_path.name, file_id)
        else:
            # Create new Drive file
            existing_id = self._drive.file_exists(drive_parent_id, file_name)
            if existing_id:
                file_id = self._drive.update_file(existing_id, local_path)
                sync_status = "updated"
            else:
                file_id = self._drive.upload_file(local_path, drive_parent_id, mime_type=mime_type)
                sync_status = "created"
            logger.info("%s Drive file: %s -> %s", sync_status.title(), local_path.name, file_id)

        entry = DriveSyncEntry(
            local_path=str(local_path),
            drive_file_id=file_id,
            drive_parent_id=drive_parent_id,
            mime_type=mime_type,
            last_synced_at=utcnow(),
            content_hash=file_hash,
            sync_status=sync_status,
        )
        upsert_drive_entry(self._db, entry)
        return entry

    def sync_raw(self, base_dir: Path, folder_id: str) -> dict:
        """Mirror data/raw/ tree to a Drive folder."""
        return self._sync_tree(base_dir, folder_id, extensions={".html", ".json", ".md"})

    def sync_curated(self, base_dir: Path, folder_id: str) -> dict:
        """Mirror data/curated/ tree to a Drive folder."""
        return self._sync_tree(base_dir, folder_id, extensions={".md", ".json"})

    def _sync_tree(
        self,
        local_base: Path,
        drive_root_id: str,
        extensions: set[str] | None = None,
    ) -> dict:
        """Recursively sync a local directory tree to Drive."""
        from ..drive.mirror import DriveMirror

        mirror = DriveMirror(drive_api=self._drive)
        return mirror.mirror_tree(local_base, drive_root_id, self._db, extensions=extensions)

    def run(self) -> dict:
        """Run full sync: raw + curated folders.

        Returns stats dict with files_created, files_updated, files_skipped.
        """
        stats = {"files_created": 0, "files_updated": 0, "files_skipped": 0}

        raw_folder_id = self._settings.google_drive_raw_folder_id
        curated_folder_id = self._settings.google_drive_curated_folder_id

        if raw_folder_id:
            raw_stats = self.sync_raw(self._settings.raw_dir, raw_folder_id)
            for k in stats:
                stats[k] += raw_stats.get(k, 0)
        else:
            logger.warning("GOOGLE_DRIVE_RAW_FOLDER_ID not set, skipping raw sync.")

        if curated_folder_id:
            curated_stats = self.sync_curated(self._settings.curated_dir, curated_folder_id)
            for k in stats:
                stats[k] += curated_stats.get(k, 0)
        else:
            logger.warning("GOOGLE_DRIVE_CURATED_FOLDER_ID not set, skipping curated sync.")

        logger.info(
            "Drive sync complete: created=%d updated=%d skipped=%d",
            stats["files_created"],
            stats["files_updated"],
            stats["files_skipped"],
        )
        return stats
