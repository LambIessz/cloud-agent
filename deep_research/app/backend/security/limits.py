from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from threading import Lock
from typing import AsyncIterator

from fastapi import HTTPException, status


class RequestBudget:
    def __init__(
        self,
        *,
        max_requests: int,
        window_seconds: float,
        max_concurrent: int,
        error_detail: str,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.error_detail = error_detail
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _check_rate(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            window = self._events[key]
            while window and (now - window[0]) > self.window_seconds:
                window.popleft()
            if len(window) >= self.max_requests:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=self.error_detail,
                )
            window.append(now)

    @asynccontextmanager
    async def slot(self, key: str) -> AsyncIterator[None]:
        self._check_rate(key)
        await self._semaphore.acquire()
        try:
            yield
        finally:
            self._semaphore.release()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


REQUEST_BUDGET = RequestBudget(
    max_requests=_env_int("DEEP_RESEARCH_RATE_LIMIT_PER_MINUTE", 60),
    window_seconds=_env_float("DEEP_RESEARCH_RATE_LIMIT_WINDOW_SECONDS", 60.0),
    max_concurrent=_env_int("DEEP_RESEARCH_MAX_CONCURRENT_REQUESTS", 4),
    error_detail="rate_limit_exceeded",
)
