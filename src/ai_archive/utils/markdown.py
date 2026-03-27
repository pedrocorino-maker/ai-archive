"""AI Archive — Markdown generation utilities."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import markdownify

if TYPE_CHECKING:
    from ..models import Conversation


def html_to_markdown(html: str) -> str:
    """Convert HTML to Markdown with sensible options."""
    md = markdownify.markdownify(
        html,
        heading_style=markdownify.ATX,
        bullets="-",
        code_language="",
        strip=["script", "style", "head", "meta", "link"],
        newline_style="backslash",
    )
    # Clean up excessive blank lines
    import re
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def _yaml_escape(value: str) -> str:
    """Escape a string value for YAML front matter."""
    value = value.replace('"', '\\"')
    return f'"{value}"'


def conversation_to_markdown(conv: "Conversation") -> str:
    """Generate full formatted Markdown with YAML front matter for a conversation."""
    lines: list[str] = []

    # YAML front matter
    lines.append("---")
    lines.append(f"id: {conv.id}")
    lines.append(f"provider: {conv.provider.value}")
    lines.append(f"title: {_yaml_escape(conv.title)}")
    lines.append(f"url: {conv.url}")
    if conv.created_at:
        lines.append(f"created_at: {conv.created_at.isoformat()}")
    if conv.updated_at:
        lines.append(f"updated_at: {conv.updated_at.isoformat()}")
    lines.append(f"extracted_at: {conv.extracted_at.isoformat()}")
    if conv.model_name:
        lines.append(f"model: {conv.model_name}")
    if conv.tags:
        tags_str = ", ".join(conv.tags)
        lines.append(f"tags: [{tags_str}]")
    if conv.primary_topic_slug:
        lines.append(f"topic: {conv.primary_topic_slug}")
    lines.append(f"message_count: {len(conv.messages)}")
    lines.append(f"content_hash: {conv.content_hash}")
    lines.append("---")
    lines.append("")

    # Title
    lines.append(f"# {conv.title or 'Untitled Conversation'}")
    lines.append("")

    # Messages
    for msg in conv.messages:
        role_label = msg.role.value.upper()
        lines.append(f"## [{role_label}]")
        if msg.timestamp:
            lines.append(f"*{msg.timestamp.isoformat()}*")
            lines.append("")
        text = msg.normalized_text or msg.raw_text
        if text:
            lines.append(text)
        # Code blocks not already in text
        for cb in msg.code_blocks:
            lang = cb.language or ""
            lines.append(f"```{lang}")
            lines.append(cb.code)
            lines.append("```")
        lines.append("")

    return "\n".join(lines)


def topic_doc_to_markdown(doc_data: dict[str, Any]) -> str:
    """Generate a curated master Markdown document for a topic."""
    lines: list[str] = []

    meta = doc_data.get("meta", {})
    title = meta.get("canonical_title") or doc_data.get("title", "Untitled Topic")
    slug = meta.get("slug", "")
    tags = meta.get("tags", [])
    providers = meta.get("providers", [])
    conversation_count = meta.get("conversation_count", 0)
    updated_at = meta.get("updated_at", datetime.utcnow().isoformat())

    # YAML front matter
    lines.append("---")
    lines.append(f"title: {_yaml_escape(title)}")
    lines.append(f"slug: {slug}")
    if tags:
        lines.append(f"tags: [{', '.join(tags)}]")
    if providers:
        lines.append(f"providers: [{', '.join(providers)}]")
    lines.append(f"conversation_count: {conversation_count}")
    lines.append(f"updated_at: {updated_at}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")

    # Executive summary
    summary = doc_data.get("executive_summary", "")
    if summary:
        lines.append("## Executive Summary")
        lines.append("")
        lines.append(summary)
        lines.append("")

    # Decisions & conclusions
    decisions = doc_data.get("decisions_conclusions", [])
    if decisions:
        lines.append("## Decisions & Conclusions")
        lines.append("")
        for item in decisions:
            lines.append(f"- {item}")
        lines.append("")

    # Best content
    best_content = doc_data.get("best_content", [])
    if best_content:
        lines.append("## Key Content")
        lines.append("")
        for item in best_content:
            conv_ref = item.get("conv_ref", "")
            text = item.get("text", "")
            if conv_ref:
                lines.append(f"### From: {conv_ref}")
                lines.append("")
            lines.append(text)
            lines.append("")

    # Useful prompts
    useful_prompts = doc_data.get("useful_prompts", [])
    if useful_prompts:
        lines.append("## Useful Prompts")
        lines.append("")
        for prompt in useful_prompts:
            lines.append(f"> {prompt}")
            lines.append("")

    # Code snippets
    code_snippets = doc_data.get("code_snippets", [])
    if code_snippets:
        lines.append("## Code Snippets")
        lines.append("")
        for snippet in code_snippets:
            lang = snippet.get("language", "")
            code = snippet.get("code", "")
            label = snippet.get("label", "")
            if label:
                lines.append(f"### {label}")
                lines.append("")
            lines.append(f"```{lang}")
            lines.append(code)
            lines.append("```")
            lines.append("")

    # Open questions
    open_questions = doc_data.get("open_questions", [])
    if open_questions:
        lines.append("## Open Questions")
        lines.append("")
        for q in open_questions:
            lines.append(f"- {q}")
        lines.append("")

    # Sources
    source_refs = doc_data.get("source_refs", [])
    if source_refs:
        lines.append("## Sources")
        lines.append("")
        for ref in source_refs:
            conv_id = ref.get("conversation_id", "")
            provider = ref.get("provider", "")
            ref_title = ref.get("title", conv_id)
            url = ref.get("url", "")
            if url:
                lines.append(f"- [{ref_title}]({url}) ({provider})")
            else:
                lines.append(f"- {ref_title} ({provider})")
        lines.append("")

    return "\n".join(lines)
