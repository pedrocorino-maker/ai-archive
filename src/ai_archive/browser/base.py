"""AI Archive — BaseBrowser: Playwright page helper methods."""
from __future__ import annotations

import asyncio
import random
from typing import Optional

from ..logging_config import get_logger

logger = get_logger("browser.base")


class BaseBrowser:
    """Provides reusable Playwright helpers wrapping a Page."""

    def __init__(self, page: object | None = None) -> None:
        self.page = page

    async def scroll_to_bottom_until_stable(
        self,
        page: object,
        max_attempts: int = 20,
        wait_ms: int = 1500,
    ) -> int:
        """Scroll to bottom repeatedly until the message count stops increasing.

        Returns the final message count (number of turns/items loaded).
        """
        prev_count = 0
        stable_rounds = 0
        required_stable = 2

        for attempt in range(max_attempts):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")  # type: ignore[attr-defined]
            await asyncio.sleep(wait_ms / 1000.0)

            # Count meaningful content blocks
            count = await page.evaluate(  # type: ignore[attr-defined]
                """() => {
                    const selectors = [
                        '[data-message-author-role]',
                        '.user-message',
                        '.assistant-message',
                        '.human-turn',
                        '.ai-turn',
                        'user-query',
                        'model-response',
                    ];
                    let max = 0;
                    for (const sel of selectors) {
                        const n = document.querySelectorAll(sel).length;
                        if (n > max) max = n;
                    }
                    return max;
                }"""
            )
            if count == prev_count and count > 0:
                stable_rounds += 1
                if stable_rounds >= required_stable:
                    logger.debug(
                        "Scroll stable at count=%d after %d attempts", count, attempt + 1
                    )
                    return count
            else:
                stable_rounds = 0
            prev_count = count

        return prev_count

    async def wait_for_selector_any(
        self,
        page: object,
        selectors: list[str],
        timeout: int = 10000,
    ) -> Optional[object]:
        """Wait for any of the given selectors to appear. Returns the first match."""
        for selector in selectors:
            try:
                el = await page.wait_for_selector(  # type: ignore[attr-defined]
                    selector, timeout=timeout
                )
                if el:
                    return el
            except Exception:
                continue
        return None

    async def safe_click(self, page: object, selectors: list[str]) -> bool:
        """Try to click the first matching element. Returns True on success."""
        for selector in selectors:
            try:
                el = await page.query_selector(selector)  # type: ignore[attr-defined]
                if el:
                    await el.click()
                    return True
            except Exception:
                continue
        return False

    async def safe_get_text(self, page: object, selectors: list[str]) -> str:
        """Return inner text of the first matching element, or empty string."""
        for selector in selectors:
            try:
                el = await page.query_selector(selector)  # type: ignore[attr-defined]
                if el:
                    text = await el.inner_text()
                    if text and text.strip():
                        return text.strip()
            except Exception:
                continue
        return ""

    async def get_inner_html(self, page: object, selector: str) -> str:
        """Return inner HTML of the first element matching selector."""
        try:
            el = await page.query_selector(selector)  # type: ignore[attr-defined]
            if el:
                return await el.inner_html()
        except Exception:
            pass
        return ""

    async def wait_with_jitter(self, min_ms: int = 600, max_ms: int = 1400) -> None:
        """Async sleep for a random duration to mimic human behavior."""
        delay_s = random.uniform(min_ms / 1000.0, max_ms / 1000.0)
        await asyncio.sleep(delay_s)
