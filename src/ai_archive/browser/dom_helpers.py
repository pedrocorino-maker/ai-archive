"""AI Archive — DOM helper functions for Playwright pages."""
from __future__ import annotations

from typing import Optional

from ..logging_config import get_logger

logger = get_logger("browser.dom_helpers")


async def extract_text_from_element(element: object) -> str:
    """Return the inner text of a Playwright ElementHandle."""
    try:
        text = await element.inner_text()  # type: ignore[attr-defined]
        return (text or "").strip()
    except Exception:
        return ""


async def find_all_matching(page: object, selectors: list[str]) -> list[object]:
    """Return all elements matching any of the given selectors."""
    for selector in selectors:
        try:
            elements = await page.query_selector_all(selector)  # type: ignore[attr-defined]
            if elements:
                return elements
        except Exception:
            continue
    return []


async def get_attribute_safe(element: object, attr: str) -> str:
    """Return the value of an attribute, or empty string on failure."""
    try:
        val = await element.get_attribute(attr)  # type: ignore[attr-defined]
        return val or ""
    except Exception:
        return ""


async def is_element_visible(page: object, selector: str) -> bool:
    """Return True if at least one element matching selector is visible."""
    try:
        el = await page.query_selector(selector)  # type: ignore[attr-defined]
        if el is None:
            return False
        return await el.is_visible()
    except Exception:
        return False


async def count_elements(page: object, selector: str) -> int:
    """Return the count of elements matching selector."""
    try:
        elements = await page.query_selector_all(selector)  # type: ignore[attr-defined]
        return len(elements)
    except Exception:
        return 0


async def get_page_full_html(page: object) -> str:
    """Return the full outer HTML of the current page."""
    try:
        return await page.content()  # type: ignore[attr-defined]
    except Exception as exc:
        logger.debug("Failed to get page HTML: %s", exc)
        return ""
