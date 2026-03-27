"""AI Archive — CurationPipeline: generate canonical topic documents."""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from ..db import (
    list_conversations,
    list_topics,
    upsert_canonical_doc,
)
from ..logging_config import get_logger
from ..models import CanonicalTopicDoc, Conversation, Message, MessageRole, TopicCluster
from ..utils.files import ensure_dir, safe_write, write_json
from ..utils.hashing import short_hash
from ..utils.markdown import topic_doc_to_markdown
from ..utils.text import score_content, truncate
from ..utils.time import utcnow

logger = get_logger("pipeline.curate")

_CONCLUSION_KEYWORDS = {
    "therefore", "thus", "in conclusion", "to summarize", "in summary",
    "finally", "the solution is", "the answer is", "as a result",
    "portanto", "assim", "em conclusão", "em resumo", "por fim",
}


class CurationPipeline:
    """Builds curated canonical documents for each topic cluster."""

    def __init__(self, settings: object | None = None) -> None:
        if settings is None:
            from ..config import get_settings
            settings = get_settings()
        self._settings = settings

    def score_message(self, msg: Message, recency_weight: float = 0.5) -> float:
        """Score a message on a 0–1 scale for curation quality."""
        has_code = len(msg.code_blocks) > 0
        text = msg.normalized_text or msg.raw_text
        text_lower = text.lower()
        has_conclusion = any(kw in text_lower for kw in _CONCLUSION_KEYWORDS)
        return score_content(
            text=text,
            has_code=has_code,
            recency_score=recency_weight,
            has_conclusion=has_conclusion,
        )

    def select_best_content(
        self,
        conversations: list[Conversation],
    ) -> dict[str, Any]:
        """Select and organize the best content from a list of conversations.

        Returns a dict with sections: executive_summary, decisions_conclusions,
        best_content, useful_prompts, code_snippets, contradictions, open_questions.
        """
        all_messages: list[tuple[Message, Conversation, float]] = []
        for i, conv in enumerate(conversations):
            n = len(conversations)
            recency = (i + 1) / n if n > 0 else 0.5
            for msg in conv.messages:
                score = self.score_message(msg, recency_weight=recency)
                all_messages.append((msg, conv, score))

        # Sort by score descending
        all_messages.sort(key=lambda x: x[2], reverse=True)

        # Executive summary: extractive from top-scored assistant messages
        exec_parts: list[str] = []
        for msg, conv, score in all_messages[:5]:
            if msg.role == MessageRole.ASSISTANT:
                text = (msg.normalized_text or msg.raw_text)[:300]
                if text:
                    exec_parts.append(text)
        executive_summary = "\n\n".join(exec_parts[:3])

        # Decisions & conclusions
        decisions: list[str] = []
        for msg, conv, score in all_messages:
            text = msg.normalized_text or msg.raw_text
            text_lower = text.lower()
            if any(kw in text_lower for kw in _CONCLUSION_KEYWORDS):
                snippet = truncate(text, 250)
                if snippet not in decisions:
                    decisions.append(snippet)
                if len(decisions) >= 10:
                    break

        # Best content (non-redundant, deduplicated by fuzzy similarity)
        best_content: list[dict] = []
        used_texts: list[str] = []
        for msg, conv, score in all_messages:
            text = msg.normalized_text or msg.raw_text
            if not text or len(text) < 20:
                continue
            # Deduplicate: skip if too similar to already-selected content
            is_redundant = any(
                fuzz.token_sort_ratio(text[:300], used[:300]) > 85
                for used in used_texts
            )
            if is_redundant:
                continue
            used_texts.append(text)
            best_content.append({
                "text": truncate(text, 500),
                "score": round(score, 3),
                "conv_ref": conv.title or conv.provider_conversation_id,
                "provider": conv.provider.value,
            })
            if len(best_content) >= 10:
                break

        # Useful prompts: top user messages
        useful_prompts: list[str] = []
        for msg, conv, score in all_messages:
            if msg.role != MessageRole.USER:
                continue
            text = msg.normalized_text or msg.raw_text
            if not text or len(text) < 10:
                continue
            snippet = truncate(text, 200)
            if snippet not in useful_prompts:
                useful_prompts.append(snippet)
            if len(useful_prompts) >= 8:
                break

        # Code snippets: all unique code blocks deduplicated by fingerprint
        code_snippets: list[dict] = []
        seen_code_hashes: set[str] = set()
        for msg, conv, score in all_messages:
            for cb in msg.code_blocks:
                fp = short_hash(cb.code.strip(), 12)
                if fp in seen_code_hashes:
                    continue
                seen_code_hashes.add(fp)
                code_snippets.append({
                    "language": cb.language,
                    "code": cb.code,
                    "label": f"From: {conv.title or conv.provider_conversation_id}",
                    "fingerprint": fp,
                })
                if len(code_snippets) >= self._settings.__dict__.get("max_snippets_per_topic", 20):
                    break

        # Contradictions: find messages with conflicting content
        contradictions: list[str] = []
        assistant_texts = [
            (msg.normalized_text or msg.raw_text)
            for msg, conv, score in all_messages
            if msg.role == MessageRole.ASSISTANT and len(msg.normalized_text or msg.raw_text) > 50
        ]
        # Simple contradiction detection: look for "actually" / "however" / "incorrect"
        conflict_markers = {"actually", "however", "on the contrary", "incorrect", "wrong",
                            "na verdade", "porém", "entretanto", "incorreto"}
        for text in assistant_texts:
            if any(marker in text.lower() for marker in conflict_markers):
                contradictions.append(truncate(text, 200))
                if len(contradictions) >= 5:
                    break

        # Open questions: user messages ending in ? without a good answer
        open_questions: list[str] = []
        for msg, conv, score in all_messages:
            if msg.role != MessageRole.USER:
                continue
            text = msg.normalized_text or msg.raw_text
            if text.strip().endswith("?") and len(text) > 15:
                open_questions.append(truncate(text, 200))
                if len(open_questions) >= 6:
                    break

        return {
            "executive_summary": executive_summary,
            "decisions_conclusions": decisions,
            "best_content": best_content,
            "useful_prompts": useful_prompts,
            "code_snippets": code_snippets,
            "contradictions": contradictions,
            "open_questions": open_questions,
        }

    def generate_canonical_doc(
        self,
        topic: TopicCluster,
        db_conn: sqlite3.Connection,
    ) -> CanonicalTopicDoc:
        """Build and write a CanonicalTopicDoc for a TopicCluster."""
        all_convs = list_conversations(db_conn)
        id_set = set(topic.conversation_ids)
        group_convs = [c for c in all_convs if c.id in id_set]

        if not group_convs:
            logger.warning("No conversations found for topic %s", topic.topic_id)

        content = self.select_best_content(group_convs)

        # Score conversations for value tier
        from .scorer import ConversationScorer
        scorer = ConversationScorer()
        scores = scorer.score_batch(group_convs)
        max_score = max((s.score for s in scores.values()), default=0.0)
        tier_counts = {1: 0, 2: 0, 3: 0}
        for s in scores.values():
            tier_counts[s.tier] += 1
        dominant_tier = min(tier_counts, key=lambda t: (tier_counts[t] == 0, -tier_counts[t]))

        providers = list({c.provider.value for c in group_convs})
        source_refs = [
            {
                "conversation_id": c.id,
                "provider": c.provider.value,
                "title": c.title,
                "url": c.url,
            }
            for c in group_convs
        ]

        now = utcnow()
        value_tag = f"tier{dominant_tier}_{'high' if dominant_tier==1 else 'medium' if dominant_tier==2 else 'low'}_value"
        enriched_tags = list(topic.tags) + [value_tag, f"score_{max_score:.2f}"]

        doc_data: dict = {
            "meta": {
                "canonical_title": topic.topic_title,
                "slug": topic.topic_slug,
                "tags": enriched_tags,
                "providers": providers,
                "conversation_count": len(group_convs),
                "value_tier": dominant_tier,
                "value_score": round(max_score, 3),
                "updated_at": now.isoformat(),
            },
            "source_refs": source_refs,
            **content,
        }

        # Write artifacts
        curated_dir = self._settings.curated_dir
        topic_dir = curated_dir / "topics" / topic.topic_slug
        ensure_dir(topic_dir)

        md_path = topic_dir / f"{topic.topic_slug}.md"
        manifest_path = topic_dir / "manifest.json"
        sources_path = topic_dir / "sources.json"
        snippets_dir = topic_dir / "snippets"

        md_content = topic_doc_to_markdown(doc_data)
        safe_write(md_path, md_content)

        write_json(manifest_path, {
            "topic_id": topic.topic_id,
            "topic_title": topic.topic_title,
            "slug": topic.topic_slug,
            "conversation_count": len(group_convs),
            "generated_at": now.isoformat(),
        })

        write_json(sources_path, source_refs)

        # Write code snippets
        if content["code_snippets"]:
            ensure_dir(snippets_dir)
            for i, snippet in enumerate(content["code_snippets"]):
                lang = snippet.get("language") or "txt"
                ext = lang if lang else "txt"
                snip_path = snippets_dir / f"snippet_{i:02d}.{ext}"
                safe_write(snip_path, snippet["code"])

        # Optional LLM refinement
        if self._settings.curation_llm_provider not in ("none", "", None):
            try:
                md_content = self._llm_refine(md_content, topic)
                safe_write(md_path, md_content)
            except Exception as exc:
                logger.warning("LLM refinement failed (graceful fallback): %s", exc)

        canonical_doc = CanonicalTopicDoc(
            topic_id=topic.topic_id,
            canonical_title=topic.topic_title,
            slug=topic.topic_slug,
            updated_at=now,
            providers=providers,
            conversation_count=len(group_convs),
            source_refs=source_refs,
            tags=topic.tags,
            markdown_path=str(md_path),
            manifest_path=str(manifest_path),
        )
        return canonical_doc

    def _llm_refine(self, markdown_content: str, topic: TopicCluster) -> str:
        """Refina o documento com LLM: gera título descritivo, extrai insights chave.

        Suporta: openai (gpt-4o / gpt-4o-mini), gemini (gemini-1.5-pro/flash).
        Configurado via settings.curation_llm_provider e curation_llm_model.
        """
        provider = (self._settings.curation_llm_provider or "").lower().strip()
        model = self._settings.curation_llm_model or ""
        api_key = self._settings.curation_llm_api_key or ""

        if provider in ("none", "", "off"):
            return markdown_content

        # Limite de tokens: enviar só o essencial
        content_snippet = markdown_content[:6000]

        prompt = (
            f"Você é um curador estratégico de conhecimento. "
            f"Analise este documento sobre o tópico '{topic.topic_title}' e:\n"
            f"1. Gere um TÍTULO DESCRITIVO em português (máx 80 chars) que capture a essência estratégica\n"
            f"2. Liste 3-5 INSIGHTS ACIONÁVEIS (o que Pedro pode fazer com isso)\n"
            f"3. Indique o POTENCIAL B2B/PRODUTO (alto/médio/baixo) e por quê\n"
            f"4. Se houver sub-ideias relevantes para o 'uber de precatório' ou legal tech, destaque\n\n"
            f"Responda em formato Markdown. Seja direto e específico.\n\n"
            f"---\n{content_snippet}\n---"
        )

        try:
            if provider == "openai":
                from openai import OpenAI  # type: ignore
                client = OpenAI(api_key=api_key)
                default_model = "gpt-4o-mini"
                resp = client.chat.completions.create(
                    model=model or default_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=1200,
                )
                refined = resp.choices[0].message.content or ""

            elif provider in ("gemini", "google"):
                import google.generativeai as genai  # type: ignore
                genai.configure(api_key=api_key)
                default_model = "gemini-1.5-flash"
                llm = genai.GenerativeModel(model or default_model)
                resp = llm.generate_content(prompt)
                refined = resp.text or ""

            else:
                logger.warning("curation_llm_provider desconhecido: %s", provider)
                return markdown_content

            if refined.strip():
                # Prepend LLM summary section to existing markdown
                header = f"\n\n## 🧠 Curadoria IA ({provider}/{model or 'default'})\n\n{refined}\n\n---\n\n"
                return header + markdown_content

        except Exception as exc:
            logger.warning("LLM refinement failed (graceful fallback): %s", exc)

        return markdown_content

    def run(self, db_conn: sqlite3.Connection) -> list[CanonicalTopicDoc]:
        """Run curation for all topic clusters in the DB."""
        topics = list_topics(db_conn)
        if not topics:
            logger.info("No topic clusters found to curate.")
            return []

        docs: list[CanonicalTopicDoc] = []
        for topic in topics:
            try:
                doc = self.generate_canonical_doc(topic, db_conn)
                upsert_canonical_doc(db_conn, doc)
                docs.append(doc)
                logger.info("Curated topic: %s", topic.topic_title)
            except Exception as exc:
                logger.warning("Failed to curate topic %s: %s", topic.topic_id, exc)

        # Remove stale topic directories whose slugs are no longer in the current
        # topic set.  Without this, directories from previous clusterings accumulate
        # on disk even though the DB no longer contains those topics.
        current_slugs = {topic.topic_slug for topic in topics}
        topics_dir = self._settings.curated_dir / "topics"
        if topics_dir.is_dir():
            for entry in topics_dir.iterdir():
                if entry.is_dir() and entry.name not in current_slugs:
                    import shutil
                    shutil.rmtree(entry)
                    logger.info("Removed stale topic directory: %s", entry.name)

        logger.info("Curation complete. %d documents generated.", len(docs))
        return docs
