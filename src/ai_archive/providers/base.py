"""AI Archive — abstract ProviderAdapter base class."""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from ..models import Conversation, Provider


class ProviderAdapter(ABC):
    """Abstract base class for provider-specific scraping adapters."""

    provider: Provider

    @abstractmethod
    async def enumerate_conversations(
        self, page: object, limit: int | None = None
    ) -> list[dict]:
        """Return list of {title, url, provider_id} dicts from the sidebar."""
        ...

    @abstractmethod
    async def extract_conversation(
        self, page: object, conv_meta: dict
    ) -> Conversation:
        """Open conversation URL and extract full content."""
        ...

    @abstractmethod
    async def detect_auth_state(self, page: object) -> tuple[bool, bool, str]:
        """Returns (is_authenticated, has_challenge, challenge_type)."""
        ...

    async def scroll_to_load_all(
        self,
        page: object,
        selector: str,
        max_attempts: int = 25,
    ) -> int:
        """Scroll down until the count of elements matching selector stabilizes.

        Returns the final count of elements.
        """
        prev_count = 0
        stable_rounds = 0
        required_stable = 2

        for attempt in range(max_attempts):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")  # type: ignore[attr-defined]
            await asyncio.sleep(1.5)

            try:
                elements = await page.query_selector_all(selector)  # type: ignore[attr-defined]
                count = len(elements)
            except Exception:
                count = prev_count

            if count == prev_count and count > 0:
                stable_rounds += 1
                if stable_rounds >= required_stable:
                    return count
            else:
                stable_rounds = 0
            prev_count = count

        return prev_count
