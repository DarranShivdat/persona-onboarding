"""Call lifecycle primitives with no Pipecat dependency (unit-tested offline).

Adapted from Penciled voice-agent `bot.py` (Darran's IP): idempotent per-call teardown
and ending only after the goodbye has played out.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

Step = Callable[[], Awaitable[Any]]


class CallTeardown:
    """Run named cleanup steps once, best-effort, in order. The first reason wins;
    concurrent callers await the same run. Every step's failure is logged, never raised."""

    def __init__(self, steps: Optional[list[tuple[str, Step]]] = None):
        self._steps: list[tuple[str, Step]] = list(steps or [])
        self._task: Optional[asyncio.Task] = None
        self.reason: Optional[str] = None
        self.failures: list[str] = []

    def add(self, name: str, step: Step) -> None:
        self._steps.append((name, step))

    @property
    def started(self) -> bool:
        return self._task is not None

    async def __call__(self, reason: str) -> None:
        if self._task is None:
            self.reason = reason
            self._task = asyncio.ensure_future(self._run())
        # shield: a caller being cancelled (e.g. the worker cancelling its own event
        # handler) must not abort the cleanup other callers are waiting on.
        await asyncio.shield(self._task)

    async def _run(self) -> None:
        logger.info(f"call teardown: {self.reason}")
        for name, step in self._steps:
            try:
                await step()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - best-effort cleanup
                self.failures.append(name)
                logger.warning(f"teardown step {name} failed: {e}")


async def end_after_playout(playout, *, queue_end: Step, torn_down: Callable[[], bool],
                            settle_secs: float = 0.8, timeout_secs: float = 20.0) -> bool:
    """Graceful goodbye: wait until the goodbye has actually played out, then end.
    Returns False (and queues nothing) if the call was torn down meanwhile."""
    await playout.wait_for_bot_turn_end(settle_secs=settle_secs, timeout_secs=timeout_secs)
    if torn_down():
        return False
    await queue_end()
    return True
