"""Tests for ai_archive.utils.hashing."""
from __future__ import annotations

import pytest

from ai_archive.utils.hashing import (
    content_fingerprint,
    conversation_hash,
    message_hash,
    sha256_file,
    sha256_text,
    short_hash,
)


def test_sha256_text_deterministic():
    """Same input always produces same hash."""
    h1 = sha256_text("hello world")
    h2 = sha256_text("hello world")
    assert h1 == h2


def test_sha256_text_hex():
    """Returns a hex string of length 64."""
    h = sha256_text("test")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_sha256_text_different_inputs():
    """Different inputs produce different hashes."""
    h1 = sha256_text("hello")
    h2 = sha256_text("world")
    assert h1 != h2


def test_sha256_text_empty_string():
    """Empty string has a known hash."""
    h = sha256_text("")
    assert len(h) == 64
    assert isinstance(h, str)


def test_sha256_file(tmp_path):
    """sha256_file returns deterministic hex hash for a file."""
    f = tmp_path / "test.txt"
    f.write_text("hello file content")
    h1 = sha256_file(f)
    h2 = sha256_file(f)
    assert h1 == h2
    assert len(h1) == 64


def test_sha256_file_different_content(tmp_path):
    """Different file content produces different hash."""
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("content A")
    f2.write_text("content B")
    assert sha256_file(f1) != sha256_file(f2)


def test_short_hash_default_length():
    """Default short hash has length 8."""
    h = short_hash("anything")
    assert len(h) == 8


def test_short_hash_custom_length():
    """short_hash respects custom length."""
    h = short_hash("test", length=16)
    assert len(h) == 16


def test_short_hash_deterministic():
    """Same input produces same short hash."""
    assert short_hash("abc") == short_hash("abc")


def test_content_fingerprint_order_independent():
    """content_fingerprint is order-independent (sorted before hashing)."""
    texts = ["alpha", "beta", "gamma"]
    h1 = content_fingerprint(texts)
    h2 = content_fingerprint(["gamma", "alpha", "beta"])
    assert h1 == h2


def test_content_fingerprint_length():
    """content_fingerprint returns 20-char hex string."""
    h = content_fingerprint(["a", "b", "c"])
    assert len(h) == 20


def test_content_fingerprint_different_inputs():
    """Different lists produce different fingerprints."""
    h1 = content_fingerprint(["a", "b"])
    h2 = content_fingerprint(["a", "c"])
    assert h1 != h2


def test_message_hash_length():
    """message_hash returns 16-char string."""
    h = message_hash("user", "hello world")
    assert len(h) == 16


def test_message_hash_deterministic():
    """Same role + text always produces same hash."""
    h1 = message_hash("user", "hello")
    h2 = message_hash("user", "hello")
    assert h1 == h2


def test_message_hash_role_matters():
    """Different roles produce different hashes."""
    h1 = message_hash("user", "hello")
    h2 = message_hash("assistant", "hello")
    assert h1 != h2


def test_conversation_hash():
    """conversation_hash is order-independent over message hashes."""
    hashes = [message_hash("user", f"msg{i}") for i in range(3)]
    h1 = conversation_hash(hashes)
    h2 = conversation_hash(list(reversed(hashes)))
    assert h1 == h2
    assert len(h1) == 20
