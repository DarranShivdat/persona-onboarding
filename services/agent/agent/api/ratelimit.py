"""Rate-limit hook. In-memory sliding window (per process); swap for a shared
backend by implementing `RateLimiter.allow`."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Callable, Protocol


class RateLimiter(Protocol):
    def allow(self, key: str) -> bool: ...


class NoLimit:
    def allow(self, key: str) -> bool:
        return True


class SlidingWindowLimiter:
    """At most `limit` hits per `window_s` per key."""

    def __init__(self, limit: int, window_s: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window_s, self.clock = limit, window_s, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True
