"""AI Archive — structured logging setup."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.logging import RichHandler

_run_id: str = uuid.uuid4().hex[:8]
_console = Console(stderr=True)
_initialized = False


def get_run_id() -> str:
    return _run_id


class JsonLineHandler(logging.Handler):
    """Writes one JSON object per log record to a .jsonl file."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(path, "a", encoding="utf-8")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry: dict[str, Any] = {
                "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "run_id": getattr(record, "run_id", _run_id),
                "provider": getattr(record, "provider", ""),
                "conversation_id": getattr(record, "conversation_id", ""),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                entry["exc_info"] = self.formatException(record.exc_info)
            self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._file.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            self._file.close()
        except Exception:
            pass
        super().close()


class ContextAdapter(logging.LoggerAdapter):
    """Injects run_id, provider, conversation_id into log records."""

    def process(self, msg: str, kwargs: Any) -> tuple[str, Any]:
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("run_id", _run_id)
        extra.setdefault("provider", "")
        extra.setdefault("conversation_id", "")
        return msg, kwargs


def setup_logging(
    logs_dir: Path,
    level: str = "INFO",
    json_logs: bool = True,
    human_logs: bool = True,
) -> None:
    global _initialized
    if _initialized:
        return
    _initialized = True

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger("ai_archive")
    root.setLevel(numeric_level)
    root.propagate = False

    if human_logs:
        rich_handler = RichHandler(
            console=_console,
            show_time=True,
            show_path=False,
            rich_tracebacks=True,
            markup=True,
        )
        rich_handler.setLevel(numeric_level)
        rich_fmt = logging.Formatter("%(message)s")
        rich_handler.setFormatter(rich_fmt)
        root.addHandler(rich_handler)

    if json_logs:
        log_path = logs_dir / f"{_run_id}.jsonl"
        json_handler = JsonLineHandler(log_path)
        json_handler.setLevel(numeric_level)
        root.addHandler(json_handler)


def get_logger(name: str, provider: str = "", conversation_id: str = "") -> ContextAdapter:
    """Return a context-aware logger for the given module name."""
    logger = logging.getLogger(f"ai_archive.{name}")
    extra = {
        "run_id": _run_id,
        "provider": provider,
        "conversation_id": conversation_id,
    }
    return ContextAdapter(logger, extra)
