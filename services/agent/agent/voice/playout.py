"""Playout tracking for the graceful goodbye (end only after the caller heard it).

Copied from Penciled voice-agent `observers.py::PlayoutObserver` (Darran's IP), adapted
so the stub turn counts as an LLM. BotStarted/StoppedSpeakingFrames come from the output
transport's real audio timing, so "not speaking" means the caller heard everything.
"""
from __future__ import annotations

import asyncio

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.services.llm_service import LLMService

from .stub_llm import StubTurnLLM

_LLM_SOURCES = (LLMService, StubTurnLLM)


class PlayoutObserver(BaseObserver):
    def __init__(self):
        super().__init__()
        self._speaking = False
        self._generating = False
        self._stops = 0
        self._changed = asyncio.Event()

    @property
    def speaking(self) -> bool:
        return self._speaking

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        # Frames are observed at every hop: gate on state transitions (Bot*) or the
        # originating service (LLM frames) to count each event once.
        if isinstance(frame, BotStartedSpeakingFrame):
            if not self._speaking:
                self._speaking = True
                self._changed.set()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            if self._speaking:
                self._speaking = False
                self._stops += 1
                self._changed.set()
        elif isinstance(frame, LLMFullResponseStartFrame) and isinstance(data.source, _LLM_SOURCES):
            self._generating = True
            self._changed.set()
        elif isinstance(frame, LLMFullResponseEndFrame) and isinstance(data.source, _LLM_SOURCES):
            self._generating = False
            self._changed.set()

    async def wait_for_bot_turn_end(self, settle_secs: float, timeout_secs: float) -> None:
        """Return once the bot stopped speaking at least once after this call began,
        no LLM response is in flight, and that held for `settle_secs`. `timeout_secs`
        is a fail-safe so a call that never speaks can't hang open."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_secs
        stops_at_start = self._stops
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.warning("PlayoutObserver: playout wait timed out; ending anyway.")
                return
            self._changed.clear()
            settled = self._stops > stops_at_start and not self._speaking and not self._generating
            wait = min(settle_secs, remaining) if settled else remaining
            try:
                await asyncio.wait_for(self._changed.wait(), wait)
            except asyncio.TimeoutError:
                return  # state held for settle_secs (or the deadline passed)
