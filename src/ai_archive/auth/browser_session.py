"""AI Archive — BrowserSession: main entrypoint for Playwright browser management."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import TracebackType
from typing import Optional

from ..logging_config import get_logger
from ..models import AuthMode, AuthStateInfo, Provider

logger = get_logger("auth.browser_session")


class BrowserSession:
    """Manages a Playwright browser context using the configured auth strategy."""

    def __init__(self, settings: object | None = None) -> None:
        if settings is None:
            from ..config import get_settings
            settings = get_settings()
        self._settings = settings
        self._playwright = None
        self._browser = None
        self._context = None
        self._page: dict[str, object] = {}  # provider -> page

    async def __aenter__(self) -> "BrowserSession":
        await self._start()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        await self._stop()

    async def _start(self) -> None:
        from playwright.async_api import async_playwright

        auth_mode = AuthMode(self._settings.auth_mode)
        self._playwright = await async_playwright().__aenter__()

        if auth_mode == AuthMode.ATTACH_CDP:
            await self._attach_cdp()
        elif auth_mode == AuthMode.MANAGED_PROFILE:
            await self._launch_managed_profile()
        elif auth_mode == AuthMode.STORAGE_STATE_ONLY:
            await self._launch_storage_state_only()
        else:
            raise ValueError(f"Unknown auth_mode: {auth_mode}")

    async def _attach_cdp(self) -> None:
        from .cdp_attach import attach_to_cdp

        cdp_url = self._settings.chrome_cdp_url
        self._browser, self._context = await attach_to_cdp(cdp_url)
        logger.info("Attached to CDP at %s", cdp_url)

    async def _launch_managed_profile(self) -> None:
        user_data_dir = str(self._settings.chrome_user_data_dir)
        channel = self._settings.chrome_channel or "chrome"
        slow_mo = self._settings.slow_mo_ms or 150

        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            channel=channel,
            headless=False,
            slow_mo=slow_mo,
            viewport={"width": 1280, "height": 900},
        )
        logger.info("Launched managed profile from %s", user_data_dir)

    async def _launch_storage_state_only(self) -> None:
        from .storage_state import load_storage_state_if_valid

        slow_mo = self._settings.slow_mo_ms or 150
        storage_path = Path(self._settings.storage_state_path)
        state = load_storage_state_if_valid(storage_path)

        launch_kwargs: dict = dict(headless=False, slow_mo=slow_mo)
        self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        ctx_kwargs: dict = dict(viewport={"width": 1280, "height": 900})
        if state:
            ctx_kwargs["storage_state"] = state
        self._context = await self._browser.new_context(**ctx_kwargs)
        logger.info("Launched browser with storage state (fresh=%s)", state is not None)

    async def _stop(self) -> None:
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.__aexit__(None, None, None)
        except Exception as exc:
            logger.debug("Error during browser session teardown: %s", exc)

    # -----------------------------------------------------------------------
    # Page management
    # -----------------------------------------------------------------------

    async def get_page(self, url: str) -> object:
        """Return a Page navigated to url. Reuses existing tabs if possible."""
        auth_mode = AuthMode(self._settings.auth_mode)

        if auth_mode == AuthMode.ATTACH_CDP:
            # Try to reuse an existing tab at that URL
            for page in self._context.pages:  # type: ignore[union-attr]
                if url in page.url:
                    return page
            page = await self._context.new_page()  # type: ignore[union-attr]
            await page.goto(url, wait_until="domcontentloaded")
            return page
        else:
            if not self._context.pages:  # type: ignore[union-attr]
                page = await self._context.new_page()  # type: ignore[union-attr]
            else:
                page = self._context.pages[0]  # type: ignore[union-attr]
            await page.goto(url, wait_until="domcontentloaded")
            return page

    async def get_provider_page(self, provider: Provider) -> object:
        """Return a Page for the given provider, reusing if possible."""
        auth_mode = AuthMode(self._settings.auth_mode)

        if auth_mode == AuthMode.ATTACH_CDP:
            from .cdp_attach import find_provider_tab, open_provider_tab
            page = await find_provider_tab(self._context, provider)
            if page is None:
                page = await open_provider_tab(self._context, provider)
            return page
        else:
            base_urls = {
                Provider.CHATGPT: self._settings.chatgpt_base_url,
                Provider.GEMINI: self._settings.gemini_base_url,
            }
            url = base_urls[provider]
            return await self.get_page(url)

    async def save_storage_state(self) -> None:
        """Persist current browser context's storage state to disk."""
        from .storage_state import save_storage_state

        path = Path(self._settings.storage_state_path)
        if self._context:
            await save_storage_state(self._context, path)

    async def detect_auth_state(self, page: object, provider: Provider) -> AuthStateInfo:
        """Detect whether the page shows an authenticated session."""
        from .manual_login import detect_challenge, _is_logged_in
        from ..models import AuthMode as AM

        is_authenticated = await _is_logged_in(page, provider)
        has_challenge, challenge_type = await detect_challenge(page)

        return AuthStateInfo(
            provider=provider,
            auth_mode=AM(self._settings.auth_mode),
            is_authenticated=is_authenticated,
            has_challenge=has_challenge,
            challenge_type=challenge_type,
            storage_state_path=str(self._settings.storage_state_path),
        )

    async def wait_for_manual_login(self, page: object, provider: Provider) -> None:
        """Prompt user for manual login and wait for completion."""
        from .manual_login import prompt_manual_login

        await prompt_manual_login(
            page,
            provider,
            interactive=self._settings.interactive,
            timeout_seconds=self._settings.login_timeout_seconds,
        )
        await self.save_storage_state()
