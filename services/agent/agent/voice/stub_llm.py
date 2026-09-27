"""Stub turn for calls without Claude (no ANTHROPIC_API_KEY, or PERSONA_VOICE_STUB_LLM=1).

Sits where the LLM sits: consumes the user aggregator's `LLMContextFrame` and emits one
short templated reply as an LLM response, so aggregation, TTS, barge-in and playout
behave exactly as with Claude. VOICE-002 replaces this slot with Pipecat Flows over the
brain; nothing here decides progress.
"""
from __future__ import annotations

from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


def last_user_text(frame: LLMContextFrame) -> str:
    for m in reversed(frame.context.get_messages()):
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c.strip()
            if isinstance(c, list):
                return " ".join(p.get("text", "") for p in c if isinstance(p, dict)).strip()
    return ""


class StubTurnLLM(FrameProcessor):
    def __init__(self, *, template: str = "Got it. You said: {text}", **kwargs):
        super().__init__(**kwargs)
        self.template = template

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            text = last_user_text(frame)
            if not text:
                return
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMTextFrame(self.template.format(text=text[:200])))
            await self.push_frame(LLMFullResponseEndFrame())
        else:
            await self.push_frame(frame, direction)
