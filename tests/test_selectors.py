"""Tests for ai_archive.browser.selectors."""
from __future__ import annotations

from pathlib import Path

import pytest

from ai_archive.browser.selectors import SelectorLoader, _BUILTIN_DEFAULTS
from ai_archive.models import Provider


# ---------------------------------------------------------------------------
# SelectorLoader with YAML files
# ---------------------------------------------------------------------------

def test_selector_loader_loads_yaml_file():
    """SelectorLoader should load selectors from the config YAML files."""
    config_dir = Path(__file__).parent.parent / "config"
    loader = SelectorLoader(config_dir=config_dir)

    selectors = loader.get_selectors(Provider.CHATGPT, "conversation_list")
    assert isinstance(selectors, list)
    assert len(selectors) > 0


def test_selector_loader_gemini_yaml():
    config_dir = Path(__file__).parent.parent / "config"
    loader = SelectorLoader(config_dir=config_dir)

    selectors = loader.get_selectors(Provider.GEMINI, "conversation_list")
    assert isinstance(selectors, list)
    assert len(selectors) > 0


def test_selector_loader_yaml_login_indicator():
    config_dir = Path(__file__).parent.parent / "config"
    loader = SelectorLoader(config_dir=config_dir)

    selectors = loader.get_selectors(Provider.CHATGPT, "login_indicator")
    assert len(selectors) > 0
    # Should contain testid or aria-label selector
    combined = " ".join(selectors)
    assert "user-menu" in combined or "account" in combined.lower() or "User" in combined


# ---------------------------------------------------------------------------
# Fallback to built-in defaults when YAML missing
# ---------------------------------------------------------------------------

def test_fallback_when_yaml_missing(tmp_path):
    """When YAML file doesn't exist, should fall back to built-in defaults."""
    loader = SelectorLoader(config_dir=tmp_path)  # empty dir, no YAML files

    selectors = loader.get_selectors(Provider.CHATGPT, "conversation_list")
    assert isinstance(selectors, list)
    assert len(selectors) > 0
    # Should be the built-in defaults
    assert selectors == _BUILTIN_DEFAULTS["chatgpt"]["conversation_list"]


def test_fallback_gemini_missing(tmp_path):
    loader = SelectorLoader(config_dir=tmp_path)
    selectors = loader.get_selectors(Provider.GEMINI, "message_assistant")
    assert len(selectors) > 0


def test_fallback_returns_empty_for_unknown_element(tmp_path):
    loader = SelectorLoader(config_dir=tmp_path)
    selectors = loader.get_selectors(Provider.CHATGPT, "nonexistent_element_xyz")
    assert selectors == []


# ---------------------------------------------------------------------------
# get_selectors returns non-empty for all critical elements
# ---------------------------------------------------------------------------

CRITICAL_ELEMENTS = [
    "conversation_list",
    "conversation_link",
    "message_user",
    "message_assistant",
    "login_indicator",
    "challenge_indicator",
]


@pytest.mark.parametrize("element", CRITICAL_ELEMENTS)
def test_chatgpt_selectors_non_empty(element):
    loader = SelectorLoader(config_dir=Path(__file__).parent.parent / "config")
    selectors = loader.get_selectors(Provider.CHATGPT, element)
    assert len(selectors) > 0, f"ChatGPT selectors for '{element}' is empty"


@pytest.mark.parametrize("element", CRITICAL_ELEMENTS)
def test_gemini_selectors_non_empty(element):
    loader = SelectorLoader(config_dir=Path(__file__).parent.parent / "config")
    selectors = loader.get_selectors(Provider.GEMINI, element)
    assert len(selectors) > 0, f"Gemini selectors for '{element}' is empty"


# ---------------------------------------------------------------------------
# Reload clears cache
# ---------------------------------------------------------------------------

def test_reload_clears_cache():
    config_dir = Path(__file__).parent.parent / "config"
    loader = SelectorLoader(config_dir=config_dir)

    # First access populates cache
    _ = loader.get_selectors(Provider.CHATGPT, "conversation_list")
    assert "chatgpt" in loader._cache

    loader.reload()
    assert "chatgpt" not in loader._cache


# ---------------------------------------------------------------------------
# Built-in defaults sanity checks
# ---------------------------------------------------------------------------

def test_builtin_defaults_have_all_providers():
    assert "chatgpt" in _BUILTIN_DEFAULTS
    assert "gemini" in _BUILTIN_DEFAULTS


def test_builtin_defaults_chatgpt_complete():
    chatgpt = _BUILTIN_DEFAULTS["chatgpt"]
    for element in CRITICAL_ELEMENTS:
        assert element in chatgpt, f"Missing '{element}' in ChatGPT built-in defaults"
        assert len(chatgpt[element]) > 0


def test_builtin_defaults_gemini_complete():
    gemini = _BUILTIN_DEFAULTS["gemini"]
    for element in CRITICAL_ELEMENTS:
        assert element in gemini, f"Missing '{element}' in Gemini built-in defaults"
        assert len(gemini[element]) > 0
