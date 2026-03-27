"""AI Archive — text processing utilities."""
from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import CodeBlock

# Titles that mean "no real title"
_EMPTY_TITLES = {
    "new chat",
    "nova conversa",
    "new conversation",
    "untitled",
    "sem título",
    "chat",
    "",
}

_CONCLUSION_KEYWORDS = {
    "therefore", "thus", "in conclusion", "to summarize", "in summary",
    "finally", "the solution is", "the answer is", "as a result",
    "portanto", "assim", "em conclusão", "em resumo", "por fim",
    "a solução é", "a resposta é", "como resultado",
}


def normalize_whitespace(text: str) -> str:
    """Collapse all whitespace sequences to single spaces and strip."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +\n", "\n", text)  # strip trailing spaces before newlines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_title(title: str, fallback_text: str = "") -> str:
    """Return a cleaned title, deriving one from fallback_text if necessary."""
    stripped = (title or "").strip()
    if stripped.lower() in _EMPTY_TITLES or not stripped:
        # Derive from fallback: take first non-empty line, truncated
        if fallback_text:
            first_line = fallback_text.strip().split("\n")[0].strip()
            first_line = re.sub(r"[^\w\s\-.,!?]", "", first_line)
            first_line = first_line[:80].strip()
            if first_line:
                return first_line
        return "Untitled Conversation"
    return stripped


def extract_code_blocks(markdown_text: str) -> list["CodeBlock"]:
    """Parse ```lang\\ncode``` blocks from markdown text."""
    from ..models import CodeBlock

    pattern = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
    results = []
    for ordinal, match in enumerate(pattern.finditer(markdown_text)):
        language = match.group(1).strip()
        code = match.group(2)
        results.append(CodeBlock(language=language, code=code, ordinal=ordinal))
    return results


def strip_html_tags(html: str) -> str:
    """Remove HTML tags and decode common entities."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&nbsp;", " ")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    return normalize_whitespace(text)


def truncate(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, appending ellipsis if needed."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 1] + "…"


def slugify(text: str) -> str:
    """Convert text to URL-safe slug: lowercase, hyphens, no special chars."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


def make_stable_slug(text: str, existing_slugs: set[str]) -> str:
    """Generate a unique slug, adding a short hash suffix on collision."""
    from .hashing import short_hash

    base = slugify(text)
    if not base:
        base = "topic"
    candidate = base
    if candidate not in existing_slugs:
        return candidate
    suffix = short_hash(text, 6)
    candidate = f"{base}-{suffix}"
    # Fallback: keep incrementing numeric suffix
    counter = 2
    while candidate in existing_slugs:
        candidate = f"{base}-{suffix}-{counter}"
        counter += 1
    return candidate


def score_content(
    text: str,
    has_code: bool,
    recency_score: float,
    has_conclusion: bool,
) -> float:
    """Return a 0–1 quality score for a piece of content."""
    score = 0.0

    # Code presence: +0.30
    if has_code:
        score += 0.30

    # Length score: +0.20 (normalized, saturates at ~2000 chars)
    length_score = min(len(text) / 2000.0, 1.0) * 0.20
    score += length_score

    # Conclusion keyword presence: +0.20
    if has_conclusion:
        score += 0.20
    else:
        text_lower = text.lower()
        if any(kw in text_lower for kw in _CONCLUSION_KEYWORDS):
            score += 0.20

    # Recency: +0.30
    score += min(max(recency_score, 0.0), 1.0) * 0.30

    return min(score, 1.0)
