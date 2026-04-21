"""异步重试装饰器，支持指数退避 + jitter。"""

import asyncio
import functools
import logging
import random
from typing import Set, Tuple, Type

import httpx

logger = logging.getLogger(__name__)

DEFAULT_RETRYABLE_STATUSES: Set[int] = {429, 500, 502, 503, 529}
DEFAULT_RETRYABLE_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, ConnectionError,
)


def async_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 16.0,
    retryable_statuses: Set[int] = DEFAULT_RETRYABLE_STATUSES,
    retryable_exceptions: Tuple[Type[Exception], ...] = DEFAULT_RETRYABLE_EXCEPTIONS,
):
    """指数退避重试。jitter 防雷群，400/401/403 不重试。"""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code not in retryable_statuses:
                        raise
                    last_exc = e
                except retryable_exceptions as e:
                    last_exc = e
                except Exception:
                    raise
                if attempt < max_attempts:
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    wait = delay + random.uniform(0, base_delay)
                    logger.warning(
                        "%s 第%d/%d次失败，%.1fs后重试: %s",
                        func.__qualname__, attempt, max_attempts, wait, last_exc,
                    )
                    await asyncio.sleep(wait)
            logger.error("%s 重试%d次后仍失败: %s", func.__qualname__, max_attempts, last_exc)
            raise last_exc
        return wrapper
    return decorator
