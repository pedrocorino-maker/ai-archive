"""AI Archive — manual Gemini download importer.

Parses saved Gemini HTML pages or JSON exports (e.g. Google Takeout) and
inserts them into the archive SQLite database as provider=gemini rows.

Supported input formats
-----------------------
HTML  — single-file saves from the Gemini web app browser (Save Page As…)
JSON  — Google Takeout bundles or any JSON with a recognisable conversation
        structure (array of conversations or single conversation object).

Usage
-----
    from ai_archive.importers.gemini_html import GeminiDownloadImporter
    importer = GeminiDownloadImporter(settings, db_conn)
    stats = importer.import_path(Path("~/Downloads"))
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

from ..db import get_conversation, upsert_conversation
from ..logging_config import get_logger
from ..models import CodeBlock, Conversation, Message, MessageRole, Provider
from ..utils.files import ensure_dir, make_conversation_raw_path, safe_write
from ..utils.text import clean_title, extract_code_blocks, normalize_whitespace
from ..utils.time import utcnow

logger = get_logger("importers.gemini_html")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ImportStats:
    imported: int = 0
    skipped: int = 0
    errors: int = 0
    error_files: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Importer
# ---------------------------------------------------------------------------

class GeminiDownloadImporter:
    """Import manually-saved Gemini conversations (HTML or JSON) into the archive."""

    def __init__(self, settings: Any, db_conn: Any) -> None:
        self._settings = settings
        self._db = db_conn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def import_path(self, source: Path) -> ImportStats:
        """Import from a single file or a directory tree.

        HTML files are processed before JSON files so that an HTML page and
        its companion *_data.json (browser "save complete") are not double-
        counted — the HTML parser handles embedded JSON blocks.
        TXT files (Gemini "Export" plain-text format) are also supported.
        """
        stats = ImportStats()
        source = Path(source).expanduser().resolve()

        if not source.exists():
            raise FileNotFoundError(f"Path not found: {source}")

        if source.is_file():
            self._import_file(source, stats)
        else:
            html_files = sorted(source.rglob("*.html")) + sorted(source.rglob("*.htm"))
            json_files = sorted(source.rglob("*.json"))
            txt_files = sorted(source.rglob("*.txt"))
            for f in html_files:
                self._import_file(f, stats)
            for f in json_files:
                self._import_file(f, stats)
            for f in txt_files:
                self._import_file(f, stats)

        return stats

    # ------------------------------------------------------------------
    # File dispatch
    # ------------------------------------------------------------------

    def _import_file(self, path: Path, stats: ImportStats) -> None:
        try:
            suffix = path.suffix.lower()
            if suffix in (".html", ".htm"):
                convs = self._parse_html(path)
            elif suffix == ".json":
                convs = self._parse_json(path)
            elif suffix == ".txt":
                convs = self._parse_txt(path)
            else:
                return

            for conv in convs:
                existing = get_conversation(
                    self._db, Provider.GEMINI.value, conv.provider_conversation_id
                )
                if existing and existing.content_hash == conv.content_hash:
                    stats.skipped += 1
                    logger.debug("Skipping unchanged %s", conv.provider_conversation_id)
                    continue

                upsert_conversation(self._db, conv)
                stats.imported += 1
                logger.info(
                    "Imported Gemini conversation %s (%s)",
                    conv.provider_conversation_id,
                    conv.title,
                )

        except Exception as exc:
            stats.errors += 1
            stats.error_files.append(str(path))
            logger.warning("Failed to import %s: %s", path, exc, exc_info=True)

    # ------------------------------------------------------------------
    # HTML parsing
    # ------------------------------------------------------------------

    def _parse_html(self, path: Path) -> list[Conversation]:
        """Parse a saved Gemini HTML file into one Conversation."""
        raw_bytes = path.read_bytes()
        soup = BeautifulSoup(raw_bytes, "html.parser")

        provider_id = self._extract_provider_id_html(soup, path)
        title = self._extract_title_html(soup)
        messages = self._extract_messages_html(soup)

        if not messages:
            logger.warning("No messages found in %s — skipping", path.name)
            return []

        # Copy raw file into archive raw dir
        now = utcnow()
        raw_path = make_conversation_raw_path(
            self._settings.raw_dir, "gemini", now.year, now.month, provider_id
        )
        ensure_dir(raw_path.parent)
        # Only overwrite if size changed (avoids clobbering identical saves)
        if not raw_path.exists() or raw_path.stat().st_size != len(raw_bytes):
            raw_path.write_bytes(raw_bytes)

        title = clean_title(title, fallback_text=messages[0].raw_text if messages else "")

        conv = Conversation(
            id=str(uuid.uuid4()).replace("-", "")[:20],
            provider=Provider.GEMINI,
            provider_conversation_id=provider_id,
            title=title,
            url=f"https://gemini.google.com/app/{provider_id}",
            extracted_at=now,
            message_count=len(messages),
            raw_html_path=str(raw_path),
            messages=messages,
        )
        conv.content_hash = conv.compute_hash()
        return [conv]

    # --- ID extraction ---

    def _extract_provider_id_html(self, soup: BeautifulSoup, path: Path) -> str:
        """Derive a stable provider_conversation_id from HTML metadata or filename."""

        # 1. <link rel="canonical" href="https://gemini.google.com/app/<id>">
        canonical = soup.find("link", rel="canonical")
        if canonical and canonical.get("href"):
            m = re.search(r"/app/([a-zA-Z0-9_-]{8,})", canonical["href"])
            if m:
                return m.group(1)

        # 2. <meta property="og:url" …>
        og_url = soup.find("meta", property="og:url")
        if og_url and og_url.get("content"):
            m = re.search(r"/app/([a-zA-Z0-9_-]{8,})", og_url["content"])
            if m:
                return m.group(1)

        # 3. data-conversation-id attribute anywhere in the DOM
        el = soup.find(attrs={"data-conversation-id": True})
        if el and isinstance(el, Tag):
            cid = el.get("data-conversation-id", "")
            if cid and re.match(r"^[a-zA-Z0-9_-]{6,}$", cid):
                return cid

        # 4. First <a href="/app/<id>"> link
        link = soup.find("a", href=re.compile(r"/app/[a-zA-Z0-9_-]{6,}"))
        if link and isinstance(link, Tag):
            m = re.search(r"/app/([a-zA-Z0-9_-]{6,})", link["href"])
            if m:
                return m.group(1)

        # 5. Inline JSON blobs inside <script> tags
        for script in soup.find_all("script"):
            text = script.get_text() or ""
            # conversationId or conversation_id patterns
            m = re.search(r'"conversationId"\s*:\s*"([a-zA-Z0-9_-]{8,})"', text)
            if not m:
                m = re.search(r'"conversation_id"\s*:\s*"([a-zA-Z0-9_-]{8,})"', text)
            if m:
                return m.group(1)

        # 6. Stable ID from filename stem minus the export-timestamp suffix.
        #    Gemini exports files like: Title_Slug-YYYY-MM-DD-HH-MM-SS.html
        #    Stripping the timestamp lets the HTML and TXT export of the same
        #    conversation share a single provider_conversation_id.
        stem = path.stem
        stem_no_ts = re.sub(r"-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$", "", stem).strip("-_")
        if stem_no_ts:
            return "gemini-" + hashlib.sha256(stem_no_ts.encode()).hexdigest()[:16]

        # 7. Fallback: short SHA-256 of first 4 KB of text content
        text_sample = soup.get_text()[:4096]
        return "gemini-" + hashlib.sha256(text_sample.encode()).hexdigest()[:16]

    # --- Title extraction ---

    def _extract_title_html(self, soup: BeautifulSoup) -> str:
        _gemini_generic = {"gemini", "google gemini", "bard", "google bard", ""}
        for selector in [".conversation-title", "h1"]:
            el = soup.select_one(selector)
            if el:
                text = el.get_text(strip=True)
                if text and text.lower() not in _gemini_generic:
                    return text
        # <title> as last resort
        title_el = soup.find("title")
        if title_el:
            raw = title_el.get_text(strip=True)
            # Strip " - Gemini" suffix
            raw = re.sub(r"\s*[–—-]\s*(Google\s+)?Gemini\s*$", "", raw, flags=re.IGNORECASE).strip()
            if raw and raw.lower() not in _gemini_generic:
                return raw
        return ""

    # --- Message extraction ---

    def _extract_messages_html(self, soup: BeautifulSoup) -> list[Message]:
        """Extract conversation messages using multiple fallback strategies."""

        # Strategy 0: real Gemini HTML export format (div.message-pair with
        # div.message-part.question / div.message-part.answer children).
        messages = self._extract_by_message_pairs(soup)
        if messages:
            return messages

        # Strategy 1: structured data-role attributes
        messages = self._extract_by_data_role(soup)
        if messages:
            return messages

        # Strategy 2: Angular-style component elements (user-query / model-response)
        messages = self._extract_by_components(soup)
        if messages:
            return messages

        # Strategy 3: CSS class patterns (.human-turn / .ai-turn, etc.)
        messages = self._extract_by_classes(soup)
        if messages:
            return messages

        # Strategy 4: Alternating paragraph fallback inside main/article
        messages = self._extract_generic_fallback(soup)
        return messages

    def _extract_by_message_pairs(self, soup: BeautifulSoup) -> list[Message]:
        """Parse the real Gemini single-file HTML export format.

        Structure:
          <div class="message-pair">
            <div class="message-part question">
              <div class="content">…user text…</div>
            </div>
            <div class="message-part answer">
              <div class="content">…model text…</div>
            </div>
          </div>
        """
        from bs4 import Comment

        pairs = soup.find_all("div", class_="message-pair")
        if not pairs:
            return []

        messages: list[Message] = []
        ordinal = 0
        for pair in pairs:
            # User turn
            q_part = pair.find("div", class_="question")
            if q_part and isinstance(q_part, Tag):
                content_el = q_part.find("div", class_="content") or q_part
                # Strip HTML comments (Angular renders lots of <!----> noise)
                for c in content_el.find_all(string=lambda t: isinstance(t, Comment)):
                    c.extract()
                text = normalize_whitespace(content_el.get_text(separator=" "))
                # Drop the "User" speaker label if it's the first word
                text = re.sub(r"^User\s+", "", text, count=1).strip()
                if text:
                    messages.append(Message(
                        role=MessageRole.USER,
                        raw_text=text,
                        code_blocks=self._extract_code_blocks_el(content_el),
                        ordinal=ordinal,
                    ))
                    ordinal += 1

            # Model turn
            a_part = pair.find("div", class_="answer")
            if a_part and isinstance(a_part, Tag):
                content_el = a_part.find("div", class_="content") or a_part
                for c in content_el.find_all(string=lambda t: isinstance(t, Comment)):
                    c.extract()
                text = normalize_whitespace(content_el.get_text(separator=" "))
                # Drop "Gemini" speaker label
                text = re.sub(r"^Gemini\s+", "", text, count=1).strip()
                if text:
                    messages.append(Message(
                        role=MessageRole.ASSISTANT,
                        raw_text=text,
                        code_blocks=self._extract_code_blocks_el(content_el),
                        ordinal=ordinal,
                    ))
                    ordinal += 1

        return messages

    def _extract_by_data_role(self, soup: BeautifulSoup) -> list[Message]:
        messages: list[Message] = []
        role_els = soup.select("[data-role]")
        for i, el in enumerate(role_els):
            if not isinstance(el, Tag):
                continue
            role_str = el.get("data-role", "")
            if role_str == "user":
                role = MessageRole.USER
            elif role_str in ("model", "assistant"):
                role = MessageRole.ASSISTANT
            else:
                continue
            text = normalize_whitespace(el.get_text(separator=" "))
            if not text:
                continue
            messages.append(Message(
                role=role,
                raw_text=text,
                code_blocks=self._extract_code_blocks_el(el),
                ordinal=i,
            ))
        return messages

    def _extract_by_components(self, soup: BeautifulSoup) -> list[Message]:
        """Match Angular-style custom elements used by Gemini web app."""
        user_selectors = [
            "user-query",
            ".query-text",
            "user-query .query-text",
            ".user-message",
            ".human-turn",
        ]
        asst_selectors = [
            "model-response",
            "model-response .response-content",
            ".model-response",
            ".response-content",
            ".ai-turn",
        ]

        user_els = self._first_matching(soup, user_selectors)
        asst_els = self._first_matching(soup, asst_selectors)
        if not user_els and not asst_els:
            return []

        messages: list[Message] = []
        ordinal = 0
        for i in range(max(len(user_els), len(asst_els))):
            if i < len(user_els):
                text = normalize_whitespace(user_els[i].get_text(separator=" "))
                if text:
                    messages.append(Message(
                        role=MessageRole.USER,
                        raw_text=text,
                        code_blocks=self._extract_code_blocks_el(user_els[i]),
                        ordinal=ordinal,
                    ))
                    ordinal += 1
            if i < len(asst_els):
                text = normalize_whitespace(asst_els[i].get_text(separator=" "))
                if text:
                    messages.append(Message(
                        role=MessageRole.ASSISTANT,
                        raw_text=text,
                        code_blocks=self._extract_code_blocks_el(asst_els[i]),
                        ordinal=ordinal,
                    ))
                    ordinal += 1
        return messages

    def _extract_by_classes(self, soup: BeautifulSoup) -> list[Message]:
        """Try CSS class pairs for alternative Gemini / Bard layouts."""
        pair_candidates = [
            (".human-turn", ".ai-turn"),
            (".user-bubble", ".model-bubble"),
            ("[class*='user-turn']", "[class*='model-turn']"),
        ]
        for user_sel, asst_sel in pair_candidates:
            user_els = soup.select(user_sel)
            asst_els = soup.select(asst_sel)
            if not user_els and not asst_els:
                continue
            messages: list[Message] = []
            ordinal = 0
            for i in range(max(len(user_els), len(asst_els))):
                if i < len(user_els):
                    text = normalize_whitespace(user_els[i].get_text(separator=" "))
                    if text:
                        messages.append(Message(
                            role=MessageRole.USER,
                            raw_text=text,
                            code_blocks=self._extract_code_blocks_el(user_els[i]),
                            ordinal=ordinal,
                        ))
                        ordinal += 1
                if i < len(asst_els):
                    text = normalize_whitespace(asst_els[i].get_text(separator=" "))
                    if text:
                        messages.append(Message(
                            role=MessageRole.ASSISTANT,
                            raw_text=text,
                            code_blocks=self._extract_code_blocks_el(asst_els[i]),
                            ordinal=ordinal,
                        ))
                        ordinal += 1
            if messages:
                return messages
        return []

    def _extract_generic_fallback(self, soup: BeautifulSoup) -> list[Message]:
        """Very coarse fallback: treat <p> blocks inside main/article as alternating turns."""
        container = soup.select_one("main, article, .conversation-content, #chat-area")
        if not container:
            return []
        paras = [
            p for p in container.find_all("p")
            if isinstance(p, Tag) and p.get_text(strip=True)
        ]
        if not paras:
            return []
        messages: list[Message] = []
        for i, p in enumerate(paras):
            role = MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT
            text = normalize_whitespace(p.get_text(separator=" "))
            if text:
                messages.append(Message(role=role, raw_text=text, ordinal=i))
        return messages

    # --- Helpers ---

    @staticmethod
    def _first_matching(soup: BeautifulSoup, selectors: list[str]) -> list[Tag]:
        for sel in selectors:
            els = soup.select(sel)
            if els:
                return [e for e in els if isinstance(e, Tag)]
        return []

    @staticmethod
    def _extract_code_blocks_el(el: Tag) -> list[CodeBlock]:
        blocks: list[CodeBlock] = []
        for i, code_el in enumerate(el.select("pre code, .code-block code")):
            if not isinstance(code_el, Tag):
                continue
            code_text = code_el.get_text()
            if not code_text.strip():
                continue
            lang = ""
            for cls in code_el.get("class") or []:
                if cls.startswith("language-"):
                    lang = cls[len("language-"):]
                    break
            blocks.append(CodeBlock(language=lang, code=code_text, ordinal=i))
        return blocks

    # ------------------------------------------------------------------
    # TXT parsing (Gemini plain-text export)
    # ------------------------------------------------------------------

    _TXT_SKIP_NAMES = {"como-usar", "readme", "license", "changelog", "todo"}

    def _parse_txt(self, path: Path) -> list[Conversation]:
        """Parse a Gemini plain-text export file.

        Skips non-conversation text files (guides, READMEs, etc.).


        Format::
            <Title line>
            Exported on: DD/MM/YYYY, HH:MM:SS

            -----------------------------------------------------

            User:
            <text>

            Gemini:
            <text>

            -----------------------------------------------------
            ...
        """
        # Skip known non-conversation files
        if path.stem.lower().replace("-", "").replace("_", "") in {
            s.replace("-", "") for s in self._TXT_SKIP_NAMES
        }:
            logger.debug("Skipping non-conversation TXT: %s", path.name)
            return []

        try:
            raw_text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.warning("Cannot read TXT %s: %s", path.name, exc)
            return []

        # Quick sanity check: must contain at least one "User:" and "Gemini:" marker
        if not (re.search(r"^User:\s*$", raw_text, re.MULTILINE | re.IGNORECASE) and
                re.search(r"^Gemini:\s*$", raw_text, re.MULTILINE | re.IGNORECASE)):
            logger.debug("TXT %s has no User:/Gemini: markers — skipping", path.name)
            return []

        lines = raw_text.splitlines()
        if not lines:
            return []

        # --- title: first non-blank line ---
        title = ""
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("Exported on"):
                title = stripped
                break

        # --- stable provider_conversation_id from stem minus timestamp ---
        stem = path.stem
        stem_no_ts = re.sub(r"-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$", "", stem).strip("-_")
        provider_id = "gemini-" + hashlib.sha256((stem_no_ts or stem).encode()).hexdigest()[:16]

        # --- split into turns ---
        # Separator line is a row of dashes (5+)
        sep_re = re.compile(r"^-{5,}\s*$")
        user_re = re.compile(r"^User:\s*$", re.IGNORECASE)
        model_re = re.compile(r"^Gemini:\s*$", re.IGNORECASE)

        messages: list[Message] = []
        ordinal = 0
        current_role: MessageRole | None = None
        current_lines: list[str] = []

        def _flush() -> None:
            nonlocal current_role, current_lines
            if current_role is not None and current_lines:
                text = normalize_whitespace(" ".join(current_lines))
                if text:
                    messages.append(Message(
                        role=current_role,
                        raw_text=text,
                        code_blocks=extract_code_blocks(text),
                        ordinal=ordinal,
                    ))
            current_role = None
            current_lines = []

        for line in lines:
            if sep_re.match(line):
                _flush()
                continue
            if user_re.match(line):
                _flush()
                current_role = MessageRole.USER
                continue
            if model_re.match(line):
                _flush()
                current_role = MessageRole.ASSISTANT
                continue
            if current_role is not None:
                current_lines.append(line)

        _flush()

        if not messages:
            logger.warning("No messages found in TXT %s — skipping", path.name)
            return []

        title = clean_title(title, fallback_text=messages[0].raw_text)

        # Copy raw file to archive raw dir
        now = utcnow()
        raw_path = make_conversation_raw_path(
            self._settings.raw_dir, "gemini", now.year, now.month, provider_id
        ).with_suffix(".txt")
        ensure_dir(raw_path.parent)
        raw_bytes = raw_text.encode("utf-8")
        if not raw_path.exists() or raw_path.stat().st_size != len(raw_bytes):
            raw_path.write_bytes(raw_bytes)

        conv = Conversation(
            id=str(uuid.uuid4()).replace("-", "")[:20],
            provider=Provider.GEMINI,
            provider_conversation_id=provider_id,
            title=title,
            url=f"https://gemini.google.com/app/{provider_id}",
            extracted_at=now,
            message_count=len(messages),
            raw_html_path=str(raw_path),
            messages=messages,
        )
        conv.content_hash = conv.compute_hash()
        return [conv]

    # ------------------------------------------------------------------
    # JSON parsing
    # ------------------------------------------------------------------

    def _parse_json(self, path: Path) -> list[Conversation]:
        """Parse a JSON export file — handles Google Takeout bundles and common formats."""
        try:
            data = json.loads(path.read_bytes())
        except json.JSONDecodeError as exc:
            logger.warning("Invalid JSON in %s: %s", path.name, exc)
            return []

        convs: list[Conversation] = []

        if isinstance(data, list):
            # Array of conversation objects
            for item in data:
                if isinstance(item, dict):
                    c = self._json_to_conversation(item, path)
                    if c:
                        convs.append(c)
        elif isinstance(data, dict):
            # Takeout-style: {"conversations": [...]}
            if "conversations" in data and isinstance(data["conversations"], list):
                for item in data["conversations"]:
                    if isinstance(item, dict):
                        c = self._json_to_conversation(item, path)
                        if c:
                            convs.append(c)
            else:
                # Single conversation object
                c = self._json_to_conversation(data, path)
                if c:
                    convs.append(c)

        return convs

    def _json_to_conversation(self, data: dict[str, Any], source_path: Path) -> Conversation | None:
        """Convert a raw conversation dict to a Conversation model."""
        # --- provider_conversation_id ---
        provider_id = (
            data.get("conversation_id")
            or data.get("conversationId")
            or data.get("id")
            or None
        )
        if not provider_id:
            content_bytes = json.dumps(data, sort_keys=True).encode()
            provider_id = "gemini-" + hashlib.sha256(content_bytes).hexdigest()[:16]

        # --- title ---
        title = data.get("title") or data.get("name") or ""

        # --- created_at ---
        created_at: datetime | None = None
        for key in ("create_time", "created_at", "timestamp", "createdAt"):
            val = data.get(key)
            if val is None:
                continue
            try:
                if isinstance(val, (int, float)):
                    created_at = datetime.fromtimestamp(val, tz=timezone.utc)
                else:
                    created_at = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                break
            except Exception:
                continue

        # --- messages ---
        raw_msgs = (
            data.get("turns")
            or data.get("messages")
            or data.get("conversation")
            or []
        )
        messages: list[Message] = []
        for i, msg in enumerate(raw_msgs):
            if not isinstance(msg, dict):
                continue
            role_str = str(msg.get("role") or msg.get("author") or "").lower()
            if role_str in ("user", "human"):
                role = MessageRole.USER
            elif role_str in ("model", "assistant", "ai", "gemini", "bard"):
                role = MessageRole.ASSISTANT
            else:
                continue

            # content can be a plain string, a list of parts, or nested dicts
            raw_content = msg.get("content") or msg.get("text") or msg.get("parts") or ""
            if isinstance(raw_content, list):
                parts: list[str] = []
                for part in raw_content:
                    if isinstance(part, str):
                        parts.append(part)
                    elif isinstance(part, dict):
                        parts.append(part.get("text") or part.get("content") or "")
                raw_content = "\n".join(parts)
            raw_text = normalize_whitespace(str(raw_content))
            if not raw_text:
                continue

            code_blocks = extract_code_blocks(raw_text)
            messages.append(Message(
                role=role,
                raw_text=raw_text,
                code_blocks=code_blocks,
                ordinal=i,
            ))

        if not messages:
            logger.debug("No messages in JSON conversation %s — skipping", provider_id)
            return None

        title = clean_title(title, fallback_text=messages[0].raw_text)

        # Store the source JSON as the raw artifact
        now = utcnow()
        raw_path = make_conversation_raw_path(
            self._settings.raw_dir, "gemini", now.year, now.month, provider_id
        )
        # Use .json extension to keep fidelity (override .html)
        raw_json_path = raw_path.with_suffix(".json")
        ensure_dir(raw_json_path.parent)
        if not raw_json_path.exists():
            raw_json_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        conv = Conversation(
            id=str(uuid.uuid4()).replace("-", "")[:20],
            provider=Provider.GEMINI,
            provider_conversation_id=str(provider_id),
            title=title,
            url=f"https://gemini.google.com/app/{provider_id}",
            created_at=created_at,
            extracted_at=now,
            message_count=len(messages),
            raw_html_path=str(raw_json_path),
            messages=messages,
        )
        conv.content_hash = conv.compute_hash()
        return conv
