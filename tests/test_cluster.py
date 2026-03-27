"""Tests for ai_archive.pipeline.cluster."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from ai_archive.db import upsert_conversation
from ai_archive.models import Conversation, Message, MessageRole, Provider
from ai_archive.pipeline.cluster import ClusterPipeline


def _make_settings():
    s = MagicMock()
    s.embedding_model = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    s.clustering_min_cluster_size = 2
    s.clustering_algorithm = "hdbscan"
    return s


def _make_conv(
    conv_id: str,
    provider_id: str,
    canonical_text: str,
    provider: Provider = Provider.CHATGPT,
) -> Conversation:
    msg = Message(
        role=MessageRole.USER,
        raw_text=canonical_text,
        normalized_text=canonical_text,
        ordinal=0,
    )
    conv = Conversation(
        id=conv_id,
        provider=provider,
        provider_conversation_id=provider_id,
        title=canonical_text[:50],
        extracted_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        messages=[msg],
        canonical_text=canonical_text,
    )
    conv.content_hash = conv.compute_hash()
    return conv


class FakeSentenceTransformer:
    """Deterministic fake embedder for tests — no actual model download needed."""

    def encode(self, texts, show_progress_bar=False, batch_size=32):
        # Return simple hash-based deterministic vectors of dim 32
        import hashlib
        result = []
        for text in texts:
            h = hashlib.md5(text.encode()).digest()
            arr = np.frombuffer(h, dtype=np.uint8).astype(np.float32)
            arr = arr / 255.0
            # Repeat to get dim=32
            arr = np.tile(arr, 2)[:32]
            result.append(arr)
        return np.array(result, dtype=np.float32)


# ---------------------------------------------------------------------------
# build_embeddings
# ---------------------------------------------------------------------------

@patch("ai_archive.pipeline.cluster.ClusterPipeline._get_model")
def test_build_embeddings_shape(mock_get_model):
    mock_get_model.return_value = FakeSentenceTransformer()
    settings = _make_settings()
    pipeline = ClusterPipeline(settings=settings)

    convs = [
        _make_conv(f"id{i}", f"pid{i}", f"Text about topic {i}")
        for i in range(5)
    ]
    embeddings = pipeline.build_embeddings(convs)
    assert embeddings.shape[0] == 5
    assert embeddings.ndim == 2


@patch("ai_archive.pipeline.cluster.ClusterPipeline._get_model")
def test_build_embeddings_empty(mock_get_model):
    mock_get_model.return_value = FakeSentenceTransformer()
    settings = _make_settings()
    pipeline = ClusterPipeline(settings=settings)

    embeddings = pipeline.build_embeddings([])
    assert embeddings.shape[0] == 0


# ---------------------------------------------------------------------------
# cluster_conversations
# ---------------------------------------------------------------------------

@patch("ai_archive.pipeline.cluster.ClusterPipeline._get_model")
def test_cluster_conversations_groups_similar(mock_get_model):
    """Similar texts should be grouped together."""
    mock_get_model.return_value = FakeSentenceTransformer()
    settings = _make_settings()
    pipeline = ClusterPipeline(settings=settings)

    # Create conversations — just need some non-empty list
    convs = [
        _make_conv(f"id{i}", f"pid{i}", f"Python programming topic {i}")
        for i in range(4)
    ]
    embeddings = pipeline.build_embeddings(convs)
    clusters = pipeline.cluster_conversations(convs, embeddings)

    # Should return a non-empty dict
    assert isinstance(clusters, dict)
    all_ids = [cid for ids in clusters.values() for cid in ids]
    assert len(all_ids) == len(convs)


@patch("ai_archive.pipeline.cluster.ClusterPipeline._get_model")
def test_cluster_single_conversation(mock_get_model):
    mock_get_model.return_value = FakeSentenceTransformer()
    settings = _make_settings()
    pipeline = ClusterPipeline(settings=settings)

    convs = [_make_conv("only1", "pid1", "single conversation")]
    embeddings = pipeline.build_embeddings(convs)
    clusters = pipeline.cluster_conversations(convs, embeddings)

    assert len(clusters) == 1
    all_ids = [cid for ids in clusters.values() for cid in ids]
    assert "only1" in all_ids


# ---------------------------------------------------------------------------
# generate_topic_metadata
# ---------------------------------------------------------------------------

@patch("ai_archive.pipeline.cluster.ClusterPipeline._get_model")
def test_generate_topic_metadata_valid(mock_get_model, tmp_db):
    mock_get_model.return_value = FakeSentenceTransformer()
    settings = _make_settings()
    pipeline = ClusterPipeline(settings=settings)

    conv = _make_conv("id1", "pid1", "Python web development with Flask")
    upsert_conversation(tmp_db, conv)

    topic = pipeline.generate_topic_metadata(["id1"], tmp_db)

    assert topic.topic_id
    assert topic.topic_title
    assert topic.topic_slug
    assert isinstance(topic.conversation_ids, list)
    assert "id1" in topic.conversation_ids


@patch("ai_archive.pipeline.cluster.ClusterPipeline._get_model")
def test_generate_topic_metadata_slug_uniqueness(mock_get_model, tmp_db):
    mock_get_model.return_value = FakeSentenceTransformer()
    settings = _make_settings()
    pipeline = ClusterPipeline(settings=settings)

    conv1 = _make_conv("id1", "pid1", "Python programming fundamentals")
    conv2 = _make_conv("id2", "pid2", "Python programming fundamentals")
    upsert_conversation(tmp_db, conv1)
    upsert_conversation(tmp_db, conv2)

    # Generate two topics with the same text -> should produce different slugs
    existing_slugs: set[str] = set()
    topic1 = pipeline.generate_topic_metadata(["id1"], tmp_db, existing_slugs)
    existing_slugs.add(topic1.topic_slug)
    topic2 = pipeline.generate_topic_metadata(["id2"], tmp_db, existing_slugs)

    assert topic1.topic_slug != topic2.topic_slug


# ---------------------------------------------------------------------------
# run (full pipeline)
# ---------------------------------------------------------------------------

@patch("ai_archive.pipeline.cluster.ClusterPipeline._get_model")
def test_run_returns_topic_list(mock_get_model, tmp_db):
    mock_get_model.return_value = FakeSentenceTransformer()
    settings = _make_settings()
    pipeline = ClusterPipeline(settings=settings)

    for i in range(3):
        conv = _make_conv(f"runid{i}", f"runpid{i}", f"Machine learning concept {i}")
        upsert_conversation(tmp_db, conv)

    topics = pipeline.run(tmp_db)
    assert isinstance(topics, list)
    assert len(topics) >= 1


@patch("ai_archive.pipeline.cluster.ClusterPipeline._get_model")
def test_run_empty_db(mock_get_model, tmp_db):
    mock_get_model.return_value = FakeSentenceTransformer()
    settings = _make_settings()
    pipeline = ClusterPipeline(settings=settings)

    topics = pipeline.run(tmp_db)
    assert topics == []
