"""Per-turn DB timing (LAT-001): how many pooled round trips a turn made and how long they took.

`collect()` installs a thread-local collector for the calling thread; `PgStore` adds each
pooled connection block's wall time to it. No collector installed = no bookkeeping.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

_local = threading.local()


@dataclass
class DbTiming:
    ms: float = 0.0
    calls: int = 0      # pooled connection blocks (≈ round trips on the hot path)


def current() -> Optional[DbTiming]:
    return getattr(_local, "timing", None)


@contextmanager
def collect() -> Iterator[DbTiming]:
    prev, t = current(), DbTiming()
    _local.timing = t
    try:
        yield t
    finally:
        _local.timing = prev


@contextmanager
def measure() -> Iterator[None]:
    t = current()
    if t is None:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        t.ms += (time.perf_counter() - start) * 1000
        t.calls += 1
