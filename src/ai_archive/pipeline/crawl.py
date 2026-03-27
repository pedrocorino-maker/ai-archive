"""AI Archive — CrawlPipeline: orchestrates crawling for all providers."""
from __future__ import annotations

import sqlite3
import traceback
import uuid
from datetime import datetime

from ..auth.browser_session import BrowserSession
from ..auth.manual_login import ChallengeDetectedError, LoginRequiredError
from ..db import (
    get_conversation,
    insert_crawl_error,
    insert_crawl_run,
    update_crawl_run,
    upsert_conversation,
)
from ..logging_config import get_logger, get_run_id
from ..models import (
    AuthMode,
    CrawlError,
    CrawlRun,
    Provider,
)
from ..utils.retry import human_jitter
from ..utils.time import utcnow

logger = get_logger("pipeline.crawl")

_ADAPTER_MAP = {
    Provider.CHATGPT: "ai_archive.providers.chatgpt:ChatGPTAdapter",
    Provider.GEMINI: "ai_archive.providers.gemini:GeminiAdapter",
}


def _load_adapter(provider: Provider, settings: object) -> object:
    module_path, class_name = _ADAPTER_MAP[provider].split(":")
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls(settings=settings)


class CrawlPipeline:
    """Orchestrates the full crawl flow across one or more providers."""

    def __init__(self, settings: object, db_conn: sqlite3.Connection) -> None:
        self._settings = settings
        self._db = db_conn

    async def run(
        self,
        providers: list[Provider],
        limit: int | None = None,
        incremental: bool = True,
        backfill: bool = False,
    ) -> CrawlRun:
        run_id = get_run_id()
        run = CrawlRun(
            run_id=run_id,
            started_at=utcnow(),
            auth_mode=AuthMode(self._settings.auth_mode),
        )
        insert_crawl_run(self._db, run)

        try:
            for provider in providers:
                await self._crawl_provider(run, provider, limit, incremental, backfill)
        except Exception as exc:
            run.error_summary = str(exc)
            logger.error("Crawl run %s failed: %s", run_id, exc)
        finally:
            run.finished_at = utcnow()
            run.success = (
                run.conversations_failed == 0
                and run.conversations_found >= 0
                and not run.error_summary
            )
            update_crawl_run(self._db, run)

        return run

    async def _crawl_provider(
        self,
        run: CrawlRun,
        provider: Provider,
        limit: int | None,
        incremental: bool,
        backfill: bool = False,
    ) -> None:
        run.provider = provider
        adapter = _load_adapter(provider, self._settings)

        async with BrowserSession(settings=self._settings) as session:
            page = await session.get_provider_page(provider)

            # Auth check
            auth_state = await session.detect_auth_state(page, provider)

            if auth_state.has_challenge:
                raise ChallengeDetectedError(
                    f"Challenge detected on {provider.value}: {auth_state.challenge_type}. "
                    "Please solve it manually, then re-run."
                )

            if not auth_state.is_authenticated:
                logger.info("Not authenticated to %s, requesting manual login.", provider.value)
                await session.wait_for_manual_login(page, provider)

            # Phase 1: sidebar harvest (ChatGPT backfill only)
            if backfill and provider == Provider.CHATGPT:
                conv_metas, harvest_duration_min, harvest_end_reason = \
                    await self._run_backfill_harvest(page)
                run.harvest_discovered = len(conv_metas)
                run.harvest_duration_minutes = harvest_duration_min
                run.harvest_end_reason = harvest_end_reason
                if limit is not None:
                    conv_metas = conv_metas[:limit]
                logger.info(
                    "Phase 1 complete: %d harvested, %.1f min, reason=%s — starting Phase 2",
                    run.harvest_discovered,
                    harvest_duration_min,
                    harvest_end_reason,
                )
            else:
                # Normal enumeration
                effective_limit = limit or self._settings.max_conversations_per_run
                conv_metas = await adapter.enumerate_conversations(page, limit=effective_limit)

            run.conversations_found += len(conv_metas)
            logger.info("Found %d conversations for %s", len(conv_metas), provider.value)

            for conv_meta in conv_metas:
                # Support both "provider_id" (normal enumeration) and "conversation_id"
                # (backfill harvest items — the harvester uses that key in its state dict)
                provider_id = conv_meta.get("provider_id") or conv_meta.get("conversation_id", "")

                try:
                    # Incremental: look up the existing record before extracting so we
                    # can compare hashes and skip unchanged conversations.
                    existing = None
                    if incremental:
                        existing = get_conversation(self._db, provider.value, provider_id)

                    conv = await adapter.extract_conversation(page, conv_meta)

                    if incremental and existing is not None:
                        if existing.content_hash == conv.content_hash:
                            logger.debug("Skipping unchanged conversation %s", provider_id)
                            await human_jitter(
                                self._settings.jitter_min_ms,
                                self._settings.jitter_max_ms,
                            )
                            continue
                        run.conversations_updated += 1
                    else:
                        run.conversations_new += 1

                    upsert_conversation(self._db, conv)

                except Exception as exc:
                    run.conversations_failed += 1
                    tb = traceback.format_exc()
                    err = CrawlError(
                        run_id=run.run_id,
                        provider=provider,
                        conversation_id="",
                        conversation_url=conv_meta.get("url", ""),
                        error_type=type(exc).__name__,
                        message=str(exc),
                        traceback=tb,
                        occurred_at=utcnow(),
                    )
                    insert_crawl_error(self._db, err)
                    logger.warning("Failed conversation %s: %s", provider_id, exc)

                await human_jitter(
                    self._settings.jitter_min_ms,
                    self._settings.jitter_max_ms,
                )

            # Persist session state
            await session.save_storage_state()

    async def _run_backfill_harvest(
        self, page: object
    ) -> tuple[list[dict], float, str]:
        """Phase 1: run the sidebar harvester and return (conv_metas, duration_min, end_reason).

        The harvester stores items under the key ``conversation_id``; we
        normalise them here to ``provider_id`` so Phase 2 extraction code
        works with a uniform dict shape regardless of how the list was built.
        """
        from datetime import datetime, timezone
        from ..providers.chatgpt_backfill import SidebarHarvester

        state_path = self._settings.state_dir / "chatgpt_backfill_index.json"
        harvester = SidebarHarvester(settings=self._settings, state_path=state_path)

        harvest_start = datetime.now(tz=timezone.utc)
        raw_list, end_reason = await harvester.run(page)
        harvest_end = datetime.now(tz=timezone.utc)
        duration_min = (harvest_end - harvest_start).total_seconds() / 60

        # Normalise key: harvester uses "conversation_id"; extractor expects "provider_id"
        conv_list = [
            {
                "provider_id": item["conversation_id"],
                "url": item["url"],
                "title": item.get("title", ""),
            }
            for item in raw_list
        ]

        logger.info(
            "Harvest state file: %s  (%d conversations ready for Phase 2)",
            state_path,
            len(conv_list),
        )
        return conv_list, duration_min, end_reason
