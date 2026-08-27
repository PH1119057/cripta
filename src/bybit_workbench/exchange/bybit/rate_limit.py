from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from time import monotonic


class AsyncRateLimiter:
    """Shared sliding-window limiter that deliberately stays below exchange limits."""

    def __init__(
        self,
        requests_per_second: int = 10,
        *,
        safety_fraction: float = 0.8,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if requests_per_second < 1:
            raise ValueError("requests_per_second must be positive")
        if not 0 < safety_fraction <= 1:
            raise ValueError("safety_fraction must be in (0, 1]")
        self.capacity = max(1, int(requests_per_second * safety_fraction))
        self._clock = clock
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                while self._timestamps and now - self._timestamps[0] >= 1:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.capacity:
                    self._timestamps.append(now)
                    return
                delay = max(0, 1 - (now - self._timestamps[0]))
                await asyncio.sleep(delay)
