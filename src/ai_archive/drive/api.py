"""AI Archive — Google Drive API wrapper."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Optional

from ..logging_config import get_logger

logger = get_logger("drive.api")

_FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveAPI:
    """Thin wrapper around google-api-python-client for Drive operations."""

    def __init__(self, credentials: object) -> None:
        self._credentials = credentials
        self._service = None

    def build_service(self) -> object:
        """Build and return the Drive API service resource."""
        if self._service is None:
            from googleapiclient.discovery import build
            self._service = build("drive", "v3", credentials=self._credentials)
        return self._service

    @property
    def service(self) -> object:
        return self.build_service()

    def create_folder(self, parent_id: str, name: str) -> str:
        """Create a folder under parent_id and return the new folder_id."""
        metadata = {
            "name": name,
            "mimeType": _FOLDER_MIME,
            "parents": [parent_id],
        }
        result = (
            self.service.files()  # type: ignore[attr-defined]
            .create(body=metadata, fields="id")
            .execute()
        )
        folder_id = result["id"]
        logger.debug("Created Drive folder '%s' -> %s", name, folder_id)
        return folder_id

    def get_or_create_folder(self, parent_id: str, name: str) -> str:
        """Return existing folder_id or create folder if it doesn't exist."""
        existing = self.file_exists(parent_id, name, mime_type=_FOLDER_MIME)
        if existing:
            return existing
        return self.create_folder(parent_id, name)

    def upload_file(
        self,
        local_path: Path,
        parent_id: str,
        mime_type: str = "text/plain",
    ) -> str:
        """Upload a local file to Drive. Returns the Drive file_id."""
        from googleapiclient.http import MediaFileUpload

        metadata = {"name": local_path.name, "parents": [parent_id]}
        media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=False)
        result = (
            self.service.files()  # type: ignore[attr-defined]
            .create(body=metadata, media_body=media, fields="id")
            .execute()
        )
        file_id = result["id"]
        logger.debug("Uploaded '%s' -> %s", local_path.name, file_id)
        return file_id

    def update_file(self, file_id: str, local_path: Path) -> str:
        """Update the content of an existing Drive file. Returns file_id."""
        from googleapiclient.http import MediaFileUpload

        # Detect mime type
        mime_type = _detect_mime(local_path)
        media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=False)
        result = (
            self.service.files()  # type: ignore[attr-defined]
            .update(fileId=file_id, media_body=media, fields="id")
            .execute()
        )
        updated_id = result["id"]
        logger.debug("Updated Drive file %s with '%s'", updated_id, local_path.name)
        return updated_id

    def file_exists(
        self,
        parent_id: str,
        name: str,
        mime_type: str | None = None,
    ) -> Optional[str]:
        """Return file_id if a file/folder with name exists in parent_id, else None."""
        q = f"'{parent_id}' in parents and name='{name}' and trashed=false"
        if mime_type:
            q += f" and mimeType='{mime_type}'"
        result = (
            self.service.files()  # type: ignore[attr-defined]
            .list(q=q, fields="files(id, name)", pageSize=1)
            .execute()
        )
        files = result.get("files", [])
        if files:
            return files[0]["id"]
        return None

    def list_files(self, folder_id: str) -> list[dict]:
        """List all files (not folders) in a Drive folder."""
        q = f"'{folder_id}' in parents and trashed=false"
        result = (
            self.service.files()  # type: ignore[attr-defined]
            .list(q=q, fields="files(id, name, mimeType, modifiedTime, size)", pageSize=1000)
            .execute()
        )
        return result.get("files", [])

    def get_file_metadata(self, file_id: str) -> dict:
        """Return metadata for a Drive file by ID."""
        result = (
            self.service.files()  # type: ignore[attr-defined]
            .get(fileId=file_id, fields="id, name, mimeType, modifiedTime, size, parents")
            .execute()
        )
        return result


def _detect_mime(path: Path) -> str:
    """Guess MIME type from file extension."""
    ext = path.suffix.lower()
    return {
        ".html": "text/html",
        ".md": "text/markdown",
        ".json": "application/json",
        ".txt": "text/plain",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }.get(ext, "application/octet-stream")
