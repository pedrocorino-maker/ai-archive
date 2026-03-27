"""AI Archive — ChatGPT provider adapter."""
from __future__ import annotations

import asyncio
import re
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..browser.base import BaseBrowser
from ..browser.dom_helpers import (
    count_elements,
    extract_text_from_element,
    find_all_matching,
    get_attribute_safe,
    get_page_full_html,
)
from ..browser.screenshots import save_diagnostic_html, take_error_screenshot
from ..browser.selectors import SelectorLoader
from ..logging_config import get_logger, get_run_id
from ..models import CodeBlock, Conversation, Message, MessageRole, Provider
from ..utils.files import ensure_dir, make_conversation_raw_path, safe_write
from ..utils.text import clean_title, normalize_whitespace
from ..utils.time import month_folder, utcnow
from .base import ProviderAdapter

logger = get_logger("providers.chatgpt")


class ChatGPTAdapter(ProviderAdapter):
    """Scraping adapter for ChatGPT (chatgpt.com)."""

    provider = Provider.CHATGPT

    def __init__(self, settings: object | None = None) -> None:
        if settings is None:
            from ..config import get_settings
            settings = get_settings()
        self._settings = settings
        self._selectors = SelectorLoader(config_dir=settings.config_dir)
        self._base = BaseBrowser()

    def _sel(self, name: str) -> list[str]:
        return self._selectors.get_selectors(Provider.CHATGPT, name)

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
        """Navigate to ChatGPT and enumerate all sidebar conversations."""
        current_url = page.url  # type: ignore[attr-defined]
        if "chatgpt.com" not in current_url:
            await page.goto(self._settings.chatgpt_base_url, wait_until="domcontentloaded")  # type: ignore[attr-defined]
            await asyncio.sleep(2.0)

        # Try to open sidebar if closed
        await self._base.safe_click(page, self._sel("sidebar_toggle"))
        await asyncio.sleep(1.0)

        # Scroll the sidebar to load all conversations
        sidebar_selectors = self._sel("conversation_list")
        primary_sidebar_sel = sidebar_selectors[0] if sidebar_selectors else "nav ol li"

        await self.scroll_to_load_all(page, primary_sidebar_sel, max_attempts=30)

        conversations = []
        link_selectors = self._sel("conversation_link")

        for sel in link_selectors:
            try:
                links = await page.query_selector_all(sel)  # type: ignore[attr-defined]
                if links:
                    for link in links:
                        href = await get_attribute_safe(link, "href")
                        if not href:
                            continue
                        # Normalize href to full URL
                        if href.startswith("/"):
                            href = f"https://chatgpt.com{href}"
                        # Extract provider_id from /c/<id>
                        m = re.search(r"/c/([a-zA-Z0-9_-]+)", href)
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
                    break
            except Exception as exc:
                logger.debug("Selector %s failed: %s", sel, exc)
                continue

        # Deduplicate by provider_id
        seen: set[str] = set()
        unique = []
        for conv in conversations:
            pid = conv["provider_id"]
            if pid not in seen:
                seen.add(pid)
                unique.append(conv)

        if limit:
            unique = unique[:limit]

        logger.info("Enumerated %d conversations from ChatGPT", len(unique))
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
            await asyncio.sleep(2.0)

            # Wait for messages to appear
            msg_selectors = self._sel("message_user") + self._sel("message_assistant")
            await self._base.wait_for_selector_any(page, msg_selectors, timeout=15000)

            # Scroll to load all messages
            all_msg_sel = "[data-message-author-role]"
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

            # Extract model name
            model_name = await self._base.safe_get_text(page, self._sel("model_name"))

            # Extract messages
            messages = await self._extract_messages(page)

            # Derive title from first user message if needed
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
                Provider.CHATGPT.value,
                now.year,
                now.month,
                provider_id,
            )
            html_content = await get_page_full_html(page)
            safe_write(raw_path, html_content)

            conv_id = str(uuid.uuid4()).replace("-", "")[:20]
            conv = Conversation(
                id=conv_id,
                provider=Provider.CHATGPT,
                provider_conversation_id=provider_id,
                title=title,
                url=url,
                extracted_at=now,
                model_name=model_name,
                message_count=len(messages),
                raw_html_path=str(raw_path),
                messages=messages,
            )
            conv.content_hash = conv.compute_hash()
            return conv

        except Exception as exc:
            # Save diagnostic artifacts
            try:
                logs_dir = self._settings.logs_dir
                await take_error_screenshot(page, logs_dir, run_id, f"chatgpt_{provider_id}")
                await save_diagnostic_html(page, logs_dir, run_id, f"chatgpt_{provider_id}")
            except Exception:
                pass

            tb = traceback.format_exc()
            logger.error(
                "Failed to extract ChatGPT conversation %s: %s", provider_id, exc
            )
            # Return a partial conversation with error note
            conv = Conversation(
                id=str(uuid.uuid4()).replace("-", "")[:20],
                provider=Provider.CHATGPT,
                provider_conversation_id=provider_id,
                title=conv_meta.get("title", ""),
                url=url,
                extracted_at=utcnow(),
                status=__import__("ai_archive.models", fromlist=["ConversationStatus"]).ConversationStatus.INCOMPLETE,
                error_note=str(exc),
            )
            raise

    async def _extract_messages(self, page: object) -> list[Message]:
        """Extract all messages from the loaded conversation page."""
        messages: list[Message] = []
        ordinal = 0

        # Try user messages
        user_elements = await find_all_matching(page, self._sel("message_user"))
        assistant_elements = await find_all_matching(page, self._sel("message_assistant"))

        # If no specific roles found, fall back to generic turn selector
        if not user_elements and not assistant_elements:
            all_turns = await page.query_selector_all("[data-message-author-role]")  # type: ignore[attr-defined]
            for el in all_turns:
                role_str = await get_attribute_safe(el, "data-message-author-role")
                role = MessageRole.USER if role_str == "user" else MessageRole.ASSISTANT
                raw_text = await extract_text_from_element(el)
                raw_text = normalize_whitespace(raw_text)
                code_blocks = await self._extract_code_blocks_from_element(el)
                msg = Message(
                    role=role,
                    raw_text=raw_text,
                    code_blocks=code_blocks,
                    ordinal=ordinal,
                )
                messages.append(msg)
                ordinal += 1
            return messages

        # Interleave user and assistant messages in DOM order
        # Use a combined approach: get all turn containers
        all_turn_elements = await page.query_selector_all(  # type: ignore[attr-defined]
            "[data-message-author-role]"
        )
        if all_turn_elements:
            for el in all_turn_elements:
                role_str = await get_attribute_safe(el, "data-message-author-role")
                if role_str == "user":
                    role = MessageRole.USER
                elif role_str == "assistant":
                    role = MessageRole.ASSISTANT
                else:
                    role = MessageRole.ASSISTANT

                raw_text = await extract_text_from_element(el)
                raw_text = normalize_whitespace(raw_text)
                code_blocks = await self._extract_code_blocks_from_element(el)

                msg = Message(
                    role=role,
                    raw_text=raw_text,
                    code_blocks=code_blocks,
                    ordinal=ordinal,
                )
                messages.append(msg)
                ordinal += 1
        else:
            # Fallback: user messages first, then assistant
            for el in user_elements:
                raw_text = normalize_whitespace(await extract_text_from_element(el))
                code_blocks = await self._extract_code_blocks_from_element(el)
                messages.append(
                    Message(
                        role=MessageRole.USER,
                        raw_text=raw_text,
                        code_blocks=code_blocks,
                        ordinal=ordinal,
                    )
                )
                ordinal += 1
            for el in assistant_elements:
                raw_text = normalize_whitespace(await extract_text_from_element(el))
                code_blocks = await self._extract_code_blocks_from_element(el)
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
                    # Try to get language from class or parent
                    lang = ""
                    try:
                        class_attr = await get_attribute_safe(code_el, "class")
                        # e.g., "language-python hljs python"
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
