"""AI Archive — ChatGPT sidebar backfill harvester (Phase 1).

Scrolls the left-sidebar scroll container slowly to force lazy-loading of
older conversations, persisting progress to disk so runs can be resumed.
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..logging_config import get_logger
from ..utils.time import utcnow

logger = get_logger("providers.chatgpt_backfill")

# ---------------------------------------------------------------------------
# State file helpers
# ---------------------------------------------------------------------------

_EMPTY_STATE: dict[str, Any] = {
    "harvest_started_at": None,
    "last_scroll_at": None,
    "current_count": 0,
    "conversations": {},
}


def load_harvest_state(path: Path) -> dict[str, Any]:
    """Load persisted harvest state from disk, or return a fresh state."""
    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
            logger.info(
                "Resumed backfill state: %d conversations already harvested",
                len(data.get("conversations", {})),
            )
            return data
        except Exception as exc:
            logger.warning("Could not load harvest state (%s), starting fresh", exc)
    return {**_EMPTY_STATE, "conversations": {}}


def save_harvest_state(path: Path, state: dict[str, Any]) -> None:
    """Persist harvest state atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# JavaScript helpers (run inside the browser)
# ---------------------------------------------------------------------------

# JS: scroll the SIDEBAR scroll container by a fraction of its height.
#
# Strategy (in priority order):
#  1. Walk up from the first visible /c/ link to find its CSS-scrollable ancestor.
#  2. Scan nav descendants for any element with overflow-y: auto/scroll
#     (no minimum height requirement — headless Chrome may not report one).
#  3. Use the ChatGPT history nav element itself as a last resort.
_JS_SCROLL_SIDEBAR = """
(scrollFraction) => {
    let target = null;
    let targetDesc = '';

    // 1. Walk up from the first conversation link — most reliable strategy.
    const firstLink = document.querySelector('a[href^="/c/"]');
    if (firstLink) {
        let el = firstLink.parentElement;
        while (el && el !== document.body) {
            const cs = window.getComputedStyle(el);
            if (cs.overflowY === 'auto' || cs.overflowY === 'scroll') {
                target = el;
                targetDesc = el.tagName + '(link-ancestor,oy=' + cs.overflowY + ')';
                break;
            }
            el = el.parentElement;
        }
    }

    // 2. Scan nav descendants for any overflow-y: auto/scroll element
    //    (skip the height check — headless may render 0px client height).
    if (!target) {
        const navRoots = [
            document.querySelector('nav[aria-label="Chat history"]'),
            document.querySelector('nav[aria-label*="history" i]'),
            document.querySelector('nav'),
        ].filter(Boolean);
        outer: for (const root of navRoots) {
            for (const child of root.querySelectorAll('*')) {
                const cs = window.getComputedStyle(child);
                if (cs.overflowY === 'auto' || cs.overflowY === 'scroll') {
                    target = child;
                    targetDesc = child.tagName + '.' + Array.from(child.classList).slice(0, 2).join('.') + '(oy=' + cs.overflowY + ')';
                    break outer;
                }
            }
        }
    }

    // 3. Fallback: use the history nav element itself (still lets us trigger lazy-load).
    if (!target) {
        const fallback = document.querySelector('nav[aria-label="Chat history"]')
                      || document.querySelector('nav');
        if (fallback) {
            target = fallback;
            targetDesc = fallback.tagName + '[fallback,sh=' + fallback.scrollHeight + ']';
        }
    }

    if (!target) {
        return {ok: false, reason: 'no sidebar container found anywhere', atBottom: false, targetDesc: ''};
    }

    const before = target.scrollTop;
    // Use a minimum of 300px so we make meaningful progress even when clientHeight is tiny.
    const step = Math.max(300, Math.floor(Math.max(target.clientHeight, 500) * scrollFraction));
    target.scrollTop += step;
    const after = target.scrollTop;
    const atBottom = (target.scrollHeight - after - target.clientHeight) < 30;

    return {
        ok: true,
        targetDesc: targetDesc,
        before: before,
        after: after,
        step: step,
        scrollHeight: target.scrollHeight,
        clientHeight: target.clientHeight,
        atBottom: atBottom,
    };
}
"""

# JS: collect all /c/<id> conversation links currently visible in the sidebar.
# Deliberately excludes GPTs (/gpts/), Sora (/sora/), and project links.
_JS_COLLECT_LINKS = """
() => {
    const results = [];
    const seen = new Set();

    // Only anchors whose href contains exactly /c/ (conversation pattern)
    const links = document.querySelectorAll('a[href*="/c/"]');
    for (const link of links) {
        const href = link.getAttribute('href') || '';

        // Must match /c/<id> — exclude /gpts/, /sora/, /projects/, /memories/, etc.
        const m = href.match(/^\\/c\\/([a-zA-Z0-9_-]{8,})$/);
        if (!m) continue;

        const id = m[1];
        if (seen.has(id)) continue;
        seen.add(id);

        const rawTitle = (link.textContent || link.innerText || '').trim();
        results.push({
            id: id,
            url: 'https://chatgpt.com' + href,
            title: rawTitle.substring(0, 300),
        });
    }
    return results;
}
"""

# JS: ensure the left sidebar is expanded so conversations are visible.
_JS_ENSURE_SIDEBAR = """
() => {
    // Check whether the history nav is present and wide enough to be open
    const historyNav = document.querySelector('nav[aria-label="Chat history"]')
                    || document.querySelector('nav[aria-label*="history" i]')
                    || document.querySelector('nav');
    const navIsVisible = historyNav && historyNav.offsetWidth > 80;

    if (navIsVisible) {
        return 'sidebar already open (width=' + (historyNav ? historyNav.offsetWidth : '?') + ')';
    }

    // Try known toggle button selectors in priority order
    const toggleSelectors = [
        '[data-testid="navigation-toggle"]',
        '[aria-label="Open sidebar"]',
        'button[aria-label*="sidebar" i]',
        'button[aria-label*="menu" i]',
        'button[class*="sidebar"]',
        'button[class*="menu"]',
    ];
    for (const sel of toggleSelectors) {
        const btn = document.querySelector(sel);
        if (btn) {
            btn.click();
            return 'clicked toggle: ' + sel;
        }
    }
    return 'no toggle found; sidebar state unknown';
}
"""


# ---------------------------------------------------------------------------
# Main harvester
# ---------------------------------------------------------------------------


class SidebarHarvester:
    """Harvests all ChatGPT conversation IDs from the sidebar by slow-scrolling.

    Phase 1 of the ChatGPT backfill pipeline.  Produces a list of
    ``{conversation_id, url, title, first_seen_at, last_seen_at}`` dicts.
    """

    def __init__(self, settings: object, state_path: Path) -> None:
        self._s = settings
        self._state_path = state_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, page: object) -> tuple[list[dict], str]:
        """Run Phase 1 harvest.

        Returns ``(conversations_list, end_reason)`` where *end_reason* is
        one of: ``"max_minutes_reached"``, ``"expected_count_reached"``,
        ``"min_duration_stagnation"``.
        """
        s = self._s
        state = load_harvest_state(self._state_path)

        # Record first-ever start in state (historical metadata only).
        if state["harvest_started_at"] is None:
            state["harvest_started_at"] = utcnow().isoformat()
            save_harvest_state(self._state_path, state)

        # Timing for THIS session always starts from now, regardless of any
        # previously persisted harvest_started_at.  This prevents a resumed
        # run from immediately hitting the max-minutes ceiling.
        session_started_at = utcnow()

        min_td = timedelta(minutes=s.chatgpt_backfill_min_minutes)
        max_td = timedelta(minutes=s.chatgpt_backfill_max_minutes)

        conversations: dict[str, dict] = dict(state.get("conversations", {}))
        new_since_batch = 0
        stagnation_count = 0
        scroll_num = 0
        end_reason = "max_minutes_reached"

        # Ensure we are on ChatGPT
        try:
            current_url = page.url  # type: ignore[attr-defined]
        except Exception:
            current_url = ""
        if "chatgpt.com" not in current_url:
            await page.goto(s.chatgpt_base_url, wait_until="domcontentloaded")  # type: ignore[attr-defined]
            await asyncio.sleep(3.0)

        # Expand the sidebar if collapsed
        try:
            sidebar_result = await page.evaluate(_JS_ENSURE_SIDEBAR)  # type: ignore[attr-defined]
            logger.info("Sidebar state: %s", sidebar_result)
            await asyncio.sleep(1.5)
        except Exception as exc:
            logger.debug("Sidebar expand attempt failed: %s", exc)

        logger.info(
            "Phase 1 harvest starting — min=%dm max=%dm stagnation_rounds=%d expected>=%d",
            s.chatgpt_backfill_min_minutes,
            s.chatgpt_backfill_max_minutes,
            s.chatgpt_backfill_stagnation_rounds,
            s.chatgpt_backfill_expected_min_conversations,
        )
        logger.info(
            "State file: %s  (resuming with %d already harvested)",
            self._state_path,
            len(conversations),
        )

        while True:
            now = utcnow()
            elapsed = now - session_started_at
            elapsed_min = elapsed.total_seconds() / 60
            scroll_num += 1

            # ---- collect links ----
            try:
                raw_links: list[dict] = await page.evaluate(_JS_COLLECT_LINKS)  # type: ignore[attr-defined]
            except Exception as exc:
                logger.warning("Link collection failed (scroll #%d): %s", scroll_num, exc)
                raw_links = []

            found_new = 0
            ts = now.isoformat()
            for item in raw_links:
                cid = item["id"]
                if cid not in conversations:
                    conversations[cid] = {
                        "conversation_id": cid,
                        "url": item["url"],
                        "title": item["title"],
                        "first_seen_at": ts,
                        "last_seen_at": ts,
                    }
                    found_new += 1
                else:
                    conversations[cid]["last_seen_at"] = ts

            total = len(conversations)

            if found_new > 0:
                stagnation_count = 0
                new_since_batch += found_new
                logger.info(
                    "Harvest [scroll #%d | %.1f min] total=%d  +%d new",
                    scroll_num,
                    elapsed_min,
                    total,
                    found_new,
                )
            else:
                stagnation_count += 1
                logger.debug(
                    "Harvest [scroll #%d | %.1f min] total=%d  no new  (stagnation %d/%d)",
                    scroll_num,
                    elapsed_min,
                    total,
                    stagnation_count,
                    s.chatgpt_backfill_stagnation_rounds,
                )

            # ---- persist state ----
            state["conversations"] = conversations
            state["last_scroll_at"] = ts
            state["current_count"] = total
            save_harvest_state(self._state_path, state)

            # ---- termination checks ----
            past_min = elapsed >= min_td
            past_max = elapsed >= max_td
            stagnated = stagnation_count >= s.chatgpt_backfill_stagnation_rounds
            exp = s.chatgpt_backfill_expected_min_conversations
            reached_target = exp > 0 and total >= exp

            if past_max:
                end_reason = "max_minutes_reached"
                break
            if past_min and reached_target:
                end_reason = "expected_count_reached"
                break
            if past_min and stagnated:
                end_reason = "min_duration_stagnation"
                break

            # ---- batch sleep (rate-limit courtesy) ----
            if new_since_batch >= s.chatgpt_backfill_batch_size:
                sleep_s = random.uniform(
                    s.chatgpt_backfill_batch_sleep_min_seconds,
                    s.chatgpt_backfill_batch_sleep_max_seconds,
                )
                logger.info(
                    "Batch of %d reached (%d total) — pausing %.1fs before next scroll",
                    s.chatgpt_backfill_batch_size,
                    total,
                    sleep_s,
                )
                await asyncio.sleep(sleep_s)
                new_since_batch = 0

            # ---- scroll sidebar ----
            scroll_fraction = random.uniform(0.60, 0.80)
            try:
                scroll_result = await page.evaluate(_JS_SCROLL_SIDEBAR, scroll_fraction)  # type: ignore[attr-defined]
                if scroll_result:
                    if not scroll_result.get("ok"):
                        logger.warning(
                            "Scroll failed: %s",
                            scroll_result.get("reason", "unknown"),
                        )
                    elif scroll_result.get("atBottom"):
                        logger.debug(
                            "Sidebar at bottom after scroll #%d (scrollTop %s→%s in %s)",
                            scroll_num,
                            scroll_result.get("before"),
                            scroll_result.get("after"),
                            scroll_result.get("targetDesc", "?"),
                        )
            except Exception as exc:
                logger.debug("Scroll step #%d failed: %s", scroll_num, exc)

            # ---- inter-scroll jitter ----
            wait_ms = random.randint(
                s.chatgpt_backfill_scroll_wait_min_ms,
                s.chatgpt_backfill_scroll_wait_max_ms,
            )
            await asyncio.sleep(wait_ms / 1000)

        elapsed_final = (utcnow() - session_started_at).total_seconds() / 60
        logger.info(
            "Phase 1 harvest complete — %d conversations in %.1f min (end_reason=%s)",
            len(conversations),
            elapsed_final,
            end_reason,
        )

        result_list = list(conversations.values())
        return result_list, end_reason


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_iso(s: str) -> datetime:
    """Parse ISO-8601 timestamp, returning a UTC-aware datetime."""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
