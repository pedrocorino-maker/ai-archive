"""AI Archive — Chrome DevTools Protocol (CDP) attachment utilities."""
from __future__ import annotations

from typing import Optional

from ..logging_config import get_logger
from ..models import Provider

logger = get_logger("auth.cdp_attach")

_PROVIDER_URL_PATTERNS = {
    Provider.CHATGPT: ["chatgpt.com", "chat.openai.com"],
    Provider.GEMINI: ["gemini.google.com", "bard.google.com"],
}


def is_cdp_available(cdp_url: str) -> bool:
    """Quick synchronous check whether a CDP endpoint is reachable."""
    try:
        import urllib.request
        req = urllib.request.urlopen(f"{cdp_url}/json/version", timeout=3)
        return req.status == 200
    except Exception as exc:
        logger.debug("CDP not available at %s: %s", cdp_url, exc)
        return False


async def attach_to_cdp(cdp_url: str) -> tuple:
    """Connect to a running Chrome instance via CDP.

    Returns (Browser, BrowserContext) tuple.
    """
    from playwright.async_api import async_playwright

    pw = await async_playwright().__aenter__()
    browser = await pw.chromium.connect_over_cdp(cdp_url)
    contexts = browser.contexts
    if contexts:
        context = contexts[0]
    else:
        context = await browser.new_context()
    logger.info("Attached to CDP at %s — %d context(s)", cdp_url, len(browser.contexts))
    return browser, context


async def find_provider_tab(context: object, provider: Provider) -> Optional[object]:
    """Find an existing page/tab that matches the provider's URL patterns."""
    patterns = _PROVIDER_URL_PATTERNS.get(provider, [])
    pages = context.pages  # type: ignore[attr-defined]
    for page in pages:
        url = page.url
        for pattern in patterns:
            if pattern in url:
                logger.debug("Found existing tab for %s: %s", provider.value, url)
                return page
    return None


async def open_provider_tab(context: object, provider: Provider) -> object:
    """Open a new tab and navigate to the provider's base URL."""
    base_urls = {
        Provider.CHATGPT: "https://chatgpt.com",
        Provider.GEMINI: "https://gemini.google.com",
    }
    url = base_urls[provider]
    page = await context.new_page()  # type: ignore[attr-defined]
    await page.goto(url, wait_until="domcontentloaded")
    logger.info("Opened new tab for %s: %s", provider.value, url)
    return page
