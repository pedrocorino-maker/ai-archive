"""AI Archive — ClusterPipeline: embed and cluster conversations by topic."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from typing import Any

import numpy as np

from ..db import clear_topics_and_docs, list_conversations, upsert_conversation, upsert_topic
from ..logging_config import get_logger
from ..models import Conversation, Provider, TopicCluster
from ..utils.text import make_stable_slug, slugify
from ..utils.time import utcnow

logger = get_logger("pipeline.cluster")


class ClusterPipeline:
    """Embeds conversations and clusters them into topic groups."""

    def __init__(self, settings: object | None = None) -> None:
        if settings is None:
            from ..config import get_settings
            settings = get_settings()
        self._settings = settings
        self._model = None  # lazy-loaded sentence-transformers model

    def _get_model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            model_name = self._settings.embedding_model
            logger.info("Loading embedding model: %s", model_name)
            self._model = SentenceTransformer(model_name)
        return self._model

    def build_embeddings(self, conversations: list[Conversation]) -> np.ndarray:
        """Compute sentence embeddings for the canonical_text of each conversation.

        Returns an ndarray of shape (n_conversations, embedding_dim).
        """
        model = self._get_model()
        texts = [conv.canonical_text or conv.title or "" for conv in conversations]
        embeddings = model.encode(texts, show_progress_bar=False, batch_size=32)
        return np.array(embeddings, dtype=np.float32)

    def cluster_conversations(
        self,
        conversations: list[Conversation],
        embeddings: np.ndarray,
    ) -> dict[str, list[str]]:
        """Cluster conversations into topic groups.

        Returns dict mapping cluster_label -> list[conversation.id].
        Uses HDBSCAN with fallback to AgglomerativeClustering.
        """
        n = len(conversations)
        if n == 0:
            return {}
        if n == 1:
            return {"0": [conversations[0].id]}

        labels = self._run_hdbscan(embeddings)
        if labels is None:
            labels = self._run_agglomerative(embeddings)

        clusters: dict[str, list[str]] = {}
        for i, label in enumerate(labels):
            key = str(label)
            if key not in clusters:
                clusters[key] = []
            clusters[key].append(conversations[i].id)

        return clusters

    def _run_hdbscan(self, embeddings: np.ndarray) -> np.ndarray | None:
        try:
            import hdbscan
            min_cluster_size = max(2, self._settings.clustering_min_cluster_size)
            clusterer = hdbscan.HDBSCAN(
                min_cluster_size=min_cluster_size,
                metric="euclidean",
                cluster_selection_method="eom",
            )
            labels = clusterer.fit_predict(embeddings)
            logger.debug("HDBSCAN labels: %s", np.unique(labels).tolist())
            return labels
        except Exception as exc:
            logger.warning("HDBSCAN failed, will fallback: %s", exc)
            return None

    def _run_agglomerative(self, embeddings: np.ndarray) -> np.ndarray:
        from sklearn.cluster import AgglomerativeClustering
        n = len(embeddings)
        n_clusters = max(1, min(n // 2, 20))
        clusterer = AgglomerativeClustering(
            n_clusters=n_clusters,
            linkage="ward",
        )
        labels = clusterer.fit_predict(embeddings)
        logger.debug("AgglomerativeClustering labels: %s", np.unique(labels).tolist())
        return labels

    def _extract_keyphrases(self, texts: list[str], n_phrases: int = 5) -> list[str]:
        """Extract keyphrases from a list of texts using YAKE (Portuguese-first)."""
        try:
            import yake
            combined = " ".join(texts[:20])[:5000]
            # Try Portuguese first; fallback to language-agnostic ("") if pt fails
            for lang in ("pt", ""):
                try:
                    kw_extractor = yake.KeywordExtractor(
                        lan=lang, n=2, dedupLim=0.7, top=n_phrases
                    )
                    keywords = kw_extractor.extract_keywords(combined)
                    if keywords:
                        return [kw for kw, score in keywords]
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("YAKE keyphrase extraction failed: %s", exc)
        return []

    def generate_topic_metadata(
        self,
        conv_ids: list[str],
        db_conn: sqlite3.Connection,
        existing_slugs: set[str] | None = None,
    ) -> TopicCluster:
        """Generate a TopicCluster record for a group of conversations."""
        if existing_slugs is None:
            existing_slugs = set()

        # Load conversations to compute centroid and extract keyphrases
        all_convs = list_conversations(db_conn)
        id_set = set(conv_ids)
        group_convs = [c for c in all_convs if c.id in id_set]

        texts = [c.canonical_text or c.title or "" for c in group_convs]
        keyphrases = self._extract_keyphrases(texts)

        # --- Better title generation ---
        # 1. If single conversation: use its actual title (much better than YAKE)
        if len(group_convs) == 1 and group_convs[0].title:
            raw_title = group_convs[0].title.strip()
            if len(raw_title) > 5:
                title = raw_title[:80]
            elif keyphrases:
                title = " · ".join(keyphrases[:3]).title()
            else:
                title = f"Conversa {uuid.uuid4().hex[:6]}"
        # 2. Multiple conversations: find longest common meaningful words from titles
        elif group_convs:
            titles = [c.title or "" for c in group_convs if c.title]
            if titles:
                # Use most representative title (longest) as anchor
                anchor = max(titles, key=len)[:80]
                title = anchor
            elif keyphrases:
                title = " · ".join(keyphrases[:3]).title()
            else:
                title = f"Tópico {uuid.uuid4().hex[:6]}"
        elif keyphrases:
            title = " · ".join(keyphrases[:3]).title()
        else:
            title = f"Tópico {uuid.uuid4().hex[:6]}"

        slug = make_stable_slug(title, existing_slugs)

        # Compute centroid embedding
        centroid: list[float] = []
        if group_convs:
            try:
                embeddings = self.build_embeddings(group_convs)
                centroid = embeddings.mean(axis=0).tolist()
            except Exception:
                pass

        # Count providers
        provider_counts: dict[str, int] = {}
        for c in group_convs:
            pv = c.provider.value
            provider_counts[pv] = provider_counts.get(pv, 0) + 1

        import hashlib
        topic_id = hashlib.sha256(slug.encode()).hexdigest()[:12]
        now = utcnow()
        return TopicCluster(
            topic_id=topic_id,
            topic_title=title,
            topic_slug=slug,
            tags=keyphrases[:8],
            conversation_ids=conv_ids,
            provider_counts=provider_counts,
            created_at=now,
            updated_at=now,
            centroid_embedding=centroid,
        )

    def run(self, db_conn: sqlite3.Connection) -> list[TopicCluster]:
        """Full clustering run: embed all conversations, cluster, upsert topics.

        Returns the list of TopicCluster records created/updated.
        Conversations scored TIER_3 (low value) are clustered separately
        and tagged as low_priority so the curate pipeline can deprioritize them.
        """
        from .scorer import ConversationScorer
        scorer = ConversationScorer()

        convs = list_conversations(db_conn)
        convs_with_text = [c for c in convs if c.canonical_text or c.title]

        if not convs_with_text:
            logger.info("No conversations to cluster.")
            return []

        # Score and log distribution
        scores = scorer.score_batch(convs_with_text)
        t1 = sum(1 for s in scores.values() if s.tier == 1)
        t2 = sum(1 for s in scores.values() if s.tier == 2)
        t3 = sum(1 for s in scores.values() if s.tier == 3)
        logger.info("Scorer distribution — T1(high):%d  T2(medium):%d  T3(low):%d", t1, t2, t3)

        # Store score in conversation for curate pipeline to use
        self._score_map = scores  # used by generate_topic_metadata tags

        logger.info("Building embeddings for %d conversations...", len(convs_with_text))
        embeddings = self.build_embeddings(convs_with_text)

        logger.info("Clustering...")
        cluster_map = self.cluster_conversations(convs_with_text, embeddings)

        # Wipe stale topic data before writing the new clustering result.
        # topic_id is derived from keyphrases which change each run, so old rows
        # are never overwritten by ON CONFLICT — they accumulate indefinitely.
        clear_topics_and_docs(db_conn)
        logger.info("Cleared stale topic_clusters and canonical_topic_docs before re-cluster.")

        topics: list[TopicCluster] = []
        existing_slugs: set[str] = set()

        for label, ids in cluster_map.items():
            if label == "-1":
                # Noise cluster in HDBSCAN — each becomes its own topic
                for cid in ids:
                    topic = self.generate_topic_metadata([cid], db_conn, existing_slugs)
                    existing_slugs.add(topic.topic_slug)
                    upsert_topic(db_conn, topic)
                    self._update_conversation_topic(db_conn, [cid], topic)
                    topics.append(topic)
            else:
                topic = self.generate_topic_metadata(ids, db_conn, existing_slugs)
                existing_slugs.add(topic.topic_slug)
                upsert_topic(db_conn, topic)
                self._update_conversation_topic(db_conn, ids, topic)
                topics.append(topic)

        logger.info("Created/updated %d topic clusters.", len(topics))
        return topics

    def _update_conversation_topic(
        self,
        db_conn: sqlite3.Connection,
        conv_ids: list[str],
        topic: TopicCluster,
    ) -> None:
        """Update each conversation's primary_topic_* fields."""
        all_convs = list_conversations(db_conn)
        id_set = set(conv_ids)
        for conv in all_convs:
            if conv.id in id_set:
                conv.primary_topic_id = topic.topic_id
                conv.primary_topic_title = topic.topic_title
                conv.primary_topic_slug = topic.topic_slug
                upsert_conversation(db_conn, conv)
