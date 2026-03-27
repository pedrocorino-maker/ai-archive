"""AI Archive — YAML-backed CSS selector profiles with built-in fallbacks."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from ..logging_config import get_logger
from ..models import Provider

logger = get_logger("browser.selectors")

_BUILTIN_DEFAULTS: dict[str, dict[str, list[str]]] = {
    Provider.CHATGPT.value: {
        "conversation_list": [
            "nav[aria-label='Chat history'] ol li",
            "nav ol li a",
            "[data-testid='history-item']",
        ],
        "conversation_link": [
            "nav ol li a",
            "a[href*='/c/']",
        ],
        "message_user": [
            "[data-message-author-role='user']",
            ".user-message",
        ],
        "message_assistant": [
            "[data-message-author-role='assistant']",
            ".assistant-message",
        ],
        "code_block": [
            "pre code",
            "code.hljs",
        ],
        "conversation_title": [
            "h1",
            "[data-testid='conversation-header']",
            "[data-testid='conversation-title']",
        ],
        "model_name": [
            "[data-testid='model-switcher-dropdown-button']",
            "button[aria-label*='model' i]",
        ],
        "login_indicator": [
            "[data-testid='user-menu-button']",
            "button[aria-label*='account' i]",
            "img[alt*='User' i]",
        ],
        "challenge_indicator": [
            "[data-testid='challenge']",
            "#challenge-form",
            "iframe[src*='challenges']",
        ],
        "sidebar_toggle": [
            "[data-testid='navigation-toggle']",
            "button[aria-label*='sidebar' i]",
        ],
    },
    Provider.GEMINI.value: {
        "conversation_list": [
            ".conversation-list-item",
            "bard-sidenav-item",
            "[data-conversation-id]",
            ".mat-list-item",
        ],
        "conversation_link": [
            "a[href*='/app/']",
            "a[href*='bard.google.com']",
            "[data-conversation-id] a",
        ],
        "message_user": [
            ".user-message",
            "[data-role='user']",
            ".human-turn",
            "user-query .query-text",
        ],
        "message_assistant": [
            ".model-response",
            "[data-role='model']",
            ".ai-turn",
            "model-response .response-content",
        ],
        "code_block": [
            "pre code",
            ".code-block code",
        ],
        "conversation_title": [
            ".conversation-title",
            "h1",
            "[aria-label*='conversation' i]",
            "bard-sidenav-item[aria-selected='true'] span",
        ],
        "login_indicator": [
            "[aria-label*='Google Account' i]",
            ".gb_A",
            "a[href*='myaccount.google.com']",
            "img[alt*='profile' i]",
        ],
        "challenge_indicator": [
            "[id='captcha']",
            "[data-action='verify']",
            "iframe[src*='recaptcha']",
        ],
    },
}


class SelectorLoader:
    """Loads CSS selector lists from YAML files with built-in fallbacks."""

    def __init__(self, config_dir: Path | None = None) -> None:
        self._config_dir = config_dir or Path("./config")
        self._cache: dict[str, dict[str, list[str]]] = {}

    def _load_yaml(self, provider: Provider) -> Optional[dict[str, list[str]]]:
        path = self._config_dir / f"selectors.{provider.value}.yaml"
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data and "selectors" in data:
                return data["selectors"]
        except Exception as exc:
            logger.warning("Failed to load selectors from %s: %s", path, exc)
        return None

    def _get_profile(self, provider: Provider) -> dict[str, list[str]]:
        key = provider.value
        if key not in self._cache:
            yaml_selectors = self._load_yaml(provider)
            if yaml_selectors:
                logger.debug("Loaded selectors from YAML for %s", provider.value)
                self._cache[key] = yaml_selectors
            else:
                logger.debug("Using built-in selector defaults for %s", provider.value)
                self._cache[key] = _BUILTIN_DEFAULTS.get(key, {})
        return self._cache[key]

    def get_selectors(self, provider: Provider, element_name: str) -> list[str]:
        """Return the selector list for the given provider and element name.

        Falls back to built-in defaults if the element is not in the YAML.
        """
        profile = self._get_profile(provider)
        selectors = profile.get(element_name)
        if selectors:
            return selectors
        # Try built-in fallback
        builtin = _BUILTIN_DEFAULTS.get(provider.value, {})
        return builtin.get(element_name, [])

    def reload(self) -> None:
        """Clear the cache and reload on next access."""
        self._cache.clear()
