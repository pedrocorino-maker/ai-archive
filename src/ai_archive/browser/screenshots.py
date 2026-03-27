"""AI Archive — screenshot and diagnostic HTML utilities."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..logging_config import get_logger

logger = get_logger("browser.screenshots")


async def take_screenshot(page: object, path: Path, label: str = "") -> None:
    """Take a full-page screenshot and save it to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        await page.screenshot(path=str(path), full_page=True)  # type: ignore[attr-defined]
        if label:
            logger.debug("Screenshot saved [%s]: %s", label, path)
        else:
            logger.debug("Screenshot saved: %s", path)
    except Exception as exc:
        logger.warning("Failed to take screenshot at %s: %s", path, exc)


async def take_error_screenshot(
    page: object,
    base_dir: Path,
    run_id: str,
    context: str,
) -> Path:
    """Take a screenshot for an error condition.

    Saves to base_dir/errors/<run_id>/<timestamp>_<context>.png
    Returns the saved path.
    """
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    safe_context = context.replace("/", "_").replace(" ", "_")[:60]
    path = base_dir / "errors" / run_id / f"{ts}_{safe_context}.png"
    await take_screenshot(page, path, label=f"error:{context}")
    return path


async def save_diagnostic_html(
    page: object,
    base_dir: Path,
    run_id: str,
    context: str,
) -> Path:
    """Save the current page HTML for diagnostic purposes.

    Saves to base_dir/errors/<run_id>/<timestamp>_<context>.html
    Returns the saved path.
    """
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    safe_context = context.replace("/", "_").replace(" ", "_")[:60]
    path = base_dir / "errors" / run_id / f"{ts}_{safe_context}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        html = await page.content()  # type: ignore[attr-defined]
        path.write_text(html, encoding="utf-8")
        logger.debug("Diagnostic HTML saved: %s", path)
    except Exception as exc:
        logger.warning("Failed to save diagnostic HTML: %s", exc)
    return path
