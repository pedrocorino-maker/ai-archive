"""AI Archive — retry utilities with tenacity and human-like jitter."""
from __future__ import annotations

import asyncio
import logging
import random
from functools import wraps
from typing import Any, Callable, TypeVar

from tenacity import (
    RetryError,
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger("ai_archive.retry")

F = TypeVar("F", bound=Callable[..., Any])


async def human_jitter(min_ms: int = 600, max_ms: int = 1400) -> None:
    """Async sleep for a random duration between min_ms and max_ms milliseconds."""
    delay_s = random.uniform(min_ms / 1000.0, max_ms / 1000.0)
    await asyncio.sleep(delay_s)


def _make_retry(
    max_attempts: int = 4,
    min_wait: float = 1.0,
    max_wait: float = 30.0,
    jitter: float = 3.0,
    exc_types: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[F], F]:
    """Factory for tenacity retry decorators."""
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=min_wait, max=max_wait, jitter=jitter),
        retry=retry_if_exception_type(exc_types),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


# Generic retry (sync or async)
with_retry = _make_retry(max_attempts=4, min_wait=1.0, max_wait=30.0, jitter=3.0)


def browser_retry(func: F) -> F:
    """Retry decorator specifically for Playwright browser operations."""
    try:
        from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout
        exc_types = (PlaywrightError, PlaywrightTimeout, Exception)
    except ImportError:
        exc_types = (Exception,)

    decorated = retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential_jitter(initial=2.0, max=20.0, jitter=2.0),
        retry=retry_if_exception_type(exc_types),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )(func)

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return await decorated(*args, **kwargs)

    return wrapper  # type: ignore[return-value]
