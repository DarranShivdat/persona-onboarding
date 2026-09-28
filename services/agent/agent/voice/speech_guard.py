"""Output guard on the call (GUARD-001): screens what the voice LLM actually says.

Sits between the LLM and TTS. The LLM's streamed text is cut into sentences (TTS
aggregates by sentence anyway, so this adds no audible latency) and each sentence goes
through the same `llm.guard.check` the text channel uses, against the corpus the flow
allows right now (`VoiceFlow.speech_context`). A violating sentence is dropped and
logged, never rewritten. If a response loses every sentence, the brain's templated
line is spoken instead, so a dropped paraphrase never becomes dead air.

`SpeechFilter` is the pure part (tested offline); `SpeechGuard` is the thin Pipecat
processor around it.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from loguru import logger

from ..llm.guard import MAX_SENTENCES, check

SpeechContext = Callable[[], Optional[dict]]
_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


class SpeechFilter:
    """Per-response sentence filter. `start()` at each LLM response, `feed()` streamed
    text (returns sentences cleared to speak), `finish()` at the end (the remainder, or
    the fallback line if everything was dropped)."""

    def __init__(self, context: SpeechContext):
        self.context = context
        self.violations: list[dict] = []
        self.start()

    def start(self) -> None:
        self._ctx = self.context()
        self._buf = ""
        self._kept = 0
        self._dropped = 0

    def feed(self, text: str) -> list[str]:
        if self._ctx is None:
            return [text]  # no flow context (not a Flows call): pass through untouched
        self._buf += text
        parts = _BOUNDARY.split(self._buf)
        self._buf = parts.pop()
        return self._screen(parts)

    def finish(self) -> list[str]:
        if self._ctx is None:
            return []
        out = self._screen([self._buf]) if self._buf.strip() else []
        self._buf = ""
        if not out and not self._kept and self._dropped and self._ctx.get("fallback"):
            out = [self._ctx["fallback"]]
            self._kept += 1
        return out

    def _screen(self, sentences: list[str]) -> list[str]:
        out = []
        for s in (x.strip() for x in sentences):
            if not s:
                continue
            reason = check(s, allowed=self._ctx.get("allowed", ""),
                           gmail_connected=bool(self._ctx.get("gmail_connected")))
            if reason is None and self._kept >= MAX_SENTENCES:
                reason = "too_long"
            if reason:
                self._dropped += 1
                self.violations.append({"type": "speech_violation", "sentence": s, "reason": reason})
                logger.warning(f"voice output guard dropped a sentence ({reason}): {s!r}")
                continue
            self._kept += 1
            out.append(s + " ")
        return out


def build_speech_guard(context: SpeechContext):
    """The Pipecat processor (lazy import: pipecat is a runtime extra)."""
    from pipecat.frames.frames import (InterruptionFrame, LLMFullResponseEndFrame,
                                       LLMFullResponseStartFrame, LLMTextFrame)
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    class SpeechGuard(FrameProcessor):
        def __init__(self):
            super().__init__(name="speech_guard")
            self.filter = SpeechFilter(context)

        async def _emit(self, texts: list[str], direction) -> None:
            for t in texts:
                await self.push_frame(LLMTextFrame(t), direction)

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if direction == FrameDirection.DOWNSTREAM:
                if isinstance(frame, (LLMFullResponseStartFrame, InterruptionFrame)):
                    self.filter.start()
                elif isinstance(frame, LLMTextFrame) and not getattr(frame, "skip_tts", False):
                    await self._emit(self.filter.feed(frame.text), direction)
                    return
                elif isinstance(frame, LLMFullResponseEndFrame):
                    await self._emit(self.filter.finish(), direction)
            await self.push_frame(frame, direction)

    return SpeechGuard()
