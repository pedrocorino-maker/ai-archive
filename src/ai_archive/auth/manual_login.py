"""AI Archive — manual login prompts and challenge detection."""
from __future__ import annotations

import asyncio
from typing import Optional

from rich.console import Console
from rich.prompt import Prompt

from ..logging_config import get_logger
from ..models import Provider

logger = get_logger("auth.manual_login")
console = Console()

_CHALLENGE_SELECTORS = [
    "#challenge-form",
    "iframe[src*='challenges']",
    "[data-testid='cf-turnstile']",
    "#captcha",
    "[data-action='verify']",
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
]

_LOGIN_INDICATORS = {
    Provider.CHATGPT: [
        "[data-testid='user-menu-button']",
        "button[aria-label*='account' i]",
        "img[alt*='User' i]",
        "nav[aria-label='Chat history']",
    ],
    Provider.GEMINI: [
        "a[aria-label*='Google Account' i]",
        ".gb_A",
        "img[alt*='profile' i]",
        "[aria-label*='My Account' i]",
    ],
}


class LoginRequiredError(Exception):
    """Raised when login is required but was not completed within the timeout."""


class ChallengeDetectedError(Exception):
    """Raised when a CAPTCHA or other challenge is detected on the page."""


async def detect_challenge(page: object) -> tuple[bool, str]:
    """Check if a challenge (CAPTCHA etc.) is present on the page.

    Returns (is_challenge: bool, challenge_type: str).
    """
    for selector in _CHALLENGE_SELECTORS:
        try:
            el = await page.query_selector(selector)  # type: ignore[attr-defined]
            if el:
                challenge_type = selector.split("[")[0].lstrip("#").strip() or "unknown"
                return True, challenge_type
        except Exception:
            continue
    return False, ""


async def _is_logged_in(page: object, provider: Provider) -> bool:
    """Return True if the page shows signs of a logged-in state."""
    selectors = _LOGIN_INDICATORS.get(provider, [])
    for selector in selectors:
        try:
            el = await page.query_selector(selector)  # type: ignore[attr-defined]
            if el:
                return True
        except Exception:
            continue
    return False


async def prompt_manual_login(
    page: object,
    provider: Provider,
    interactive: bool = True,
    timeout_seconds: int = 300,
) -> None:
    """Wait for the user to complete manual login.

    If interactive=True, prints a terminal prompt and also polls the page.
    Raises LoginRequiredError if login is not detected within timeout_seconds.
    Raises ChallengeDetectedError if a CAPTCHA is detected.
    """
    # Check if already logged in
    if await _is_logged_in(page, provider):
        logger.info("Already logged in to %s", provider.value)
        return

    # Check for challenge before login attempt
    has_challenge, challenge_type = await detect_challenge(page)
    if has_challenge:
        raise ChallengeDetectedError(
            f"Challenge detected on {provider.value} page: {challenge_type}. "
            "Please solve it manually and re-run."
        )

    if interactive:
        console.print(
            f"\n[bold yellow]Manual login required for [cyan]{provider.value}[/cyan].[/bold yellow]\n"
            "Please log in in the browser window, then press [bold green]ENTER[/bold green] here,\n"
            "or just wait — login will be auto-detected.\n"
        )

    # Poll loop — auto-detects login
    poll_interval = 2.0
    elapsed = 0.0

    enter_task: Optional[asyncio.Task] = None
    if interactive:
        loop = asyncio.get_event_loop()
        enter_task = loop.run_in_executor(None, input, "")

    while elapsed < timeout_seconds:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        if await _is_logged_in(page, provider):
            logger.info("Login detected for %s after %.1fs", provider.value, elapsed)
            if enter_task and not enter_task.done():
                enter_task.cancel()
            return

        has_challenge, challenge_type = await detect_challenge(page)
        if has_challenge:
            if enter_task and not enter_task.done():
                enter_task.cancel()
            raise ChallengeDetectedError(
                f"Challenge detected on {provider.value}: {challenge_type}"
            )

        # Check if user pressed ENTER
        if enter_task and enter_task.done():
            if await _is_logged_in(page, provider):
                return
            # User pressed ENTER but not logged in yet — re-prompt
            console.print(
                "[yellow]Not logged in yet. Continuing to wait...[/yellow]"
            )
            loop = asyncio.get_event_loop()
            enter_task = loop.run_in_executor(None, input, "")

    if enter_task and not enter_task.done():
        enter_task.cancel()

    raise LoginRequiredError(
        f"Login not completed for {provider.value} within {timeout_seconds}s timeout."
    )
