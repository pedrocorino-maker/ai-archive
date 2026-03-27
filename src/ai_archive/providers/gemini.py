"""AI Archive — Gemini provider adapter."""
from __future__ import annotations

import asyncio
import re
import traceback
import uuid
from pathlib import Path

from ..browser.base import BaseBrowser
from ..browser.dom_helpers import (
    extract_text_from_element,
    find_all_matching,
    get_attribute_safe,
    get_page_full_html,
)
from ..browser.screenshots import save_diagnostic_html, take_error_screenshot
from ..browser.selectors import SelectorLoader
from ..logging_config import get_logger, get_run_id
from ..models import CodeBlock, Conversation, ConversationStatus, Message, MessageRole, Provider
from ..utils.files import ensure_dir, make_conversation_raw_path, safe_write
from ..utils.text import clean_title, normalize_whitespace
from ..utils.time import utcnow
from .base import ProviderAdapter

logger = get_logger("providers.gemini")


class GeminiAdapter(ProviderAdapter):
    """Scraping adapter for Google Gemini (gemini.google.com)."""

    provider = Provider.GEMINI

    def __init__(self, settings: object | None = None) -> None:
        if settings is None:
            from ..config import get_settings
            settings = get_settings()
        self._settings = settings
        self._selectors = SelectorLoader(config_dir=settings.config_dir)
        self._base = BaseBrowser()

    def _sel(self, name: str) -> list[str]:
        return self._selectors.get_selectors(Provider.GEMINI, name)

    # -----------------------------------------------------------------------
    # Auth detection
    # -----------------------------------------------------------------------

    async def detect_auth_state(self, page: object) -> tuple[bool, bool, str]:
        login_selectors = self._sel("login_indicator")
        is_authenticated = False
        for sel in login_selectors:
            try:
                el = await page.query_selector(sel)  # type: ignore[attr-defined]
                if el:
                    is_authenticated = True
                    break
            except Exception:
                pass

        challenge_selectors = self._sel("challenge_indicator")
        has_challenge = False
        challenge_type = ""
        for sel in challenge_selectors:
            try:
                el = await page.query_selector(sel)  # type: ignore[attr-defined]
                if el:
                    has_challenge = True
                    challenge_type = sel
                    break
            except Exception:
                pass

        return is_authenticated, has_challenge, challenge_type

    # -----------------------------------------------------------------------
    # Enumerate conversations
    # -----------------------------------------------------------------------

    async def enumerate_conversations(
        self, page: object, limit: int | None = None
    ) -> list[dict]:
        """Navigate to Gemini and enumerate available conversations."""
        current_url: str = page.url  # type: ignore[attr-defined]
        if "gemini.google.com" not in current_url and "bard.google.com" not in current_url:
            await page.goto(self._settings.gemini_base_url, wait_until="domcontentloaded")  # type: ignore[attr-defined]
            await asyncio.sleep(3.0)

        # Wait for the conversation list to appear
        conv_list_selectors = self._sel("conversation_list")
        await self._base.wait_for_selector_any(page, conv_list_selectors, timeout=15000)

        # Scroll to load all conversations
        primary_sel = conv_list_selectors[0] if conv_list_selectors else ".conversation-list-item"
        await self.scroll_to_load_all(page, primary_sel, max_attempts=20)

        conversations: list[dict] = []
        link_selectors = self._sel("conversation_link")

        # Try link-based extraction first
        for sel in link_selectors:
            try:
                links = await page.query_selector_all(sel)  # type: ignore[attr-defined]
                if not links:
                    continue
                for link in links:
                    href = await get_attribute_safe(link, "href")
                    if not href:
                        continue
                    if href.startswith("/"):
                        href = f"https://gemini.google.com{href}"
                    # Extract conversation ID from /app/<id>
                    m = re.search(r"/app/([a-zA-Z0-9_-]+)", href)
                    if not m:
                        # Try data-conversation-id on parent
                        try:
                            parent = await link.evaluate_handle("el => el.closest('[data-conversation-id]')")  # type: ignore[attr-defined]
                            if parent:
                                conv_id_attr = await get_attribute_safe(parent, "data-conversation-id")
                                if conv_id_attr:
                                    href = f"https://gemini.google.com/app/{conv_id_attr}"
                                    m = re.search(r"/app/([a-zA-Z0-9_-]+)", href)
                        except Exception:
                            pass
                    if not m:
                        continue
                    provider_id = m.group(1)
                    title = await extract_text_from_element(link)
                    title = clean_title(title)
                    conversations.append({
                        "title": title,
                        "url": href,
                        "provider_id": provider_id,
                    })
                if conversations:
                    break
            except Exception as exc:
                logger.debug("Selector %s failed: %s", sel, exc)
                continue

        # Fallback: try data-conversation-id attribute elements
        if not conversations:
            for sel in conv_list_selectors:
                try:
                    items = await page.query_selector_all(sel)  # type: ignore[attr-defined]
                    if not items:
                        continue
                    for item in items:
                        conv_id_attr = await get_attribute_safe(item, "data-conversation-id")
                        if not conv_id_attr:
                            continue
                        title = await extract_text_from_element(item)
                        title = clean_title(title)
                        conversations.append({
                            "title": title,
                            "url": f"https://gemini.google.com/app/{conv_id_attr}",
                            "provider_id": conv_id_attr,
                        })
                    if conversations:
                        break
                except Exception:
                    continue

        # Deduplicate
        seen: set[str] = set()
        unique: list[dict] = []
        for conv in conversations:
            pid = conv["provider_id"]
            if pid not in seen:
                seen.add(pid)
                unique.append(conv)

        if limit:
            unique = unique[:limit]

        logger.info("Enumerated %d conversations from Gemini", len(unique))
        return unique

    # -----------------------------------------------------------------------
    # Extract conversation
    # -----------------------------------------------------------------------

    async def extract_conversation(
        self, page: object, conv_meta: dict
    ) -> Conversation:
        url = conv_meta["url"]
        provider_id = conv_meta["provider_id"]
        run_id = get_run_id()

        try:
            await page.goto(url, wait_until="domcontentloaded")  # type: ignore[attr-defined]
            await asyncio.sleep(3.0)

            # Wait for content to load
            msg_selectors = self._sel("message_user") + self._sel("message_assistant")
            await self._base.wait_for_selector_any(page, msg_selectors, timeout=20000)

            # Scroll to load all messages
            await self._base.scroll_to_bottom_until_stable(
                page,
                max_attempts=self._settings.scroll_attempts,
                wait_ms=self._settings.scroll_wait_ms,
            )

            # Extract title
            title_raw = await self._base.safe_get_text(page, self._sel("conversation_title"))
            if not title_raw:
                try:
                    title_raw = await page.title()  # type: ignore[attr-defined]
                except Exception:
                    title_raw = ""

            # Extract messages
            messages = await self._extract_messages(page)

            first_user_text = ""
            for m in messages:
                if m.role == MessageRole.USER:
                    first_user_text = m.raw_text
                    break
            title = clean_title(title_raw, fallback_text=first_user_text)

            # Save raw HTML
            now = utcnow()
            raw_path = make_conversation_raw_path(
                self._settings.raw_dir,
                Provider.GEMINI.value,
                now.year,
                now.month,
                provider_id,
            )
            html_content = await get_page_full_html(page)
            safe_write(raw_path, html_content)

            conv_id = str(uuid.uuid4()).replace("-", "")[:20]
            conv = Conversation(
                id=conv_id,
                provider=Provider.GEMINI,
                provider_conversation_id=provider_id,
                title=title,
                url=url,
                extracted_at=now,
                message_count=len(messages),
                raw_html_path=str(raw_path),
                messages=messages,
            )
            conv.content_hash = conv.compute_hash()
            return conv

        except Exception as exc:
            try:
                logs_dir = self._settings.logs_dir
                await take_error_screenshot(page, logs_dir, run_id, f"gemini_{provider_id}")
                await save_diagnostic_html(page, logs_dir, run_id, f"gemini_{provider_id}")
            except Exception:
                pass

            logger.error("Failed to extract Gemini conversation %s: %s", provider_id, exc)
            raise

    async def _extract_messages(self, page: object) -> list[Message]:
        """Extract all messages from the loaded Gemini conversation page."""
        messages: list[Message] = []
        ordinal = 0

        # Try to get user and model turns
        user_elements = await find_all_matching(page, self._sel("message_user"))
        model_elements = await find_all_matching(page, self._sel("message_assistant"))

        # Try structured extraction via data-role attributes
        all_role_elements = await page.query_selector_all("[data-role]")  # type: ignore[attr-defined]
        if all_role_elements:
            for el in all_role_elements:
                role_str = await get_attribute_safe(el, "data-role")
                if role_str == "user":
                    role = MessageRole.USER
                elif role_str in ("model", "assistant"):
                    role = MessageRole.ASSISTANT
                else:
                    continue
                raw_text = normalize_whitespace(await extract_text_from_element(el))
                code_blocks = await self._extract_code_blocks_from_element(el)
                messages.append(
                    Message(
                        role=role,
                        raw_text=raw_text,
                        code_blocks=code_blocks,
                        ordinal=ordinal,
                    )
                )
                ordinal += 1
            if messages:
                return messages

        # Fallback: interleave user and model elements assuming alternating order
        max_len = max(len(user_elements), len(model_elements))
        for i in range(max_len):
            if i < len(user_elements):
                raw_text = normalize_whitespace(await extract_text_from_element(user_elements[i]))
                code_blocks = await self._extract_code_blocks_from_element(user_elements[i])
                messages.append(
                    Message(
                        role=MessageRole.USER,
                        raw_text=raw_text,
                        code_blocks=code_blocks,
                        ordinal=ordinal,
                    )
                )
                ordinal += 1
            if i < len(model_elements):
                raw_text = normalize_whitespace(await extract_text_from_element(model_elements[i]))
                code_blocks = await self._extract_code_blocks_from_element(model_elements[i])
                messages.append(
                    Message(
                        role=MessageRole.ASSISTANT,
                        raw_text=raw_text,
                        code_blocks=code_blocks,
                        ordinal=ordinal,
                    )
                )
                ordinal += 1

        return messages

    async def _extract_code_blocks_from_element(self, el: object) -> list[CodeBlock]:
        """Extract code blocks from a message element."""
        code_blocks: list[CodeBlock] = []
        code_selectors = self._sel("code_block")
        for sel in code_selectors:
            try:
                code_els = await el.query_selector_all(sel)  # type: ignore[attr-defined]
                for i, code_el in enumerate(code_els):
                    code_text = await extract_text_from_element(code_el)
                    if not code_text.strip():
                        continue
                    lang = ""
                    try:
                        class_attr = await get_attribute_safe(code_el, "class")
                        for cls in (class_attr or "").split():
                            if cls.startswith("language-"):
                                lang = cls[len("language-"):]
                                break
                    except Exception:
                        pass
                    code_blocks.append(CodeBlock(language=lang, code=code_text, ordinal=i))
                if code_blocks:
                    break
            except Exception:
                continue
        return code_blocks
