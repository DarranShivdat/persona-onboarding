"""Quick spoken ack while extraction runs (LAT-003). Behind `PERSONA_VOICE_QUICK_ACK`,
default OFF (Darran has not decided on the conversational feel).

Why: on a caller turn the brain's line can only be spoken after the extraction tool call
completes (~1 s of Claude), so "caller stops -> first audio" is ~2.3 s. A short ack
("Got it.") spoken the moment the LLM starts puts audio in the caller's ear at ~1 s; the
brain's line follows when it is ready. Same idea as Penciled's pre-tool-call speech, but the
words are chosen by code from a fixed list, never generated.

Rules (all enforced here, tested offline in tests/test_voice_quick_ack.py):
- only when extraction is *expected* to take longer than `threshold_ms` (a moving average of
  this call's measured extraction times, seeded from recent production timing);
- never on the greeting (no caller turn), never around the name read-back or after the call
  has graduated (`allowed()` = `VoiceFlow.ack_allowed`);
- varied, and never the same ack twice in a row;
- barge-in safe: never while the caller is speaking, at most one per caller turn, disarmed by
  any interruption, and the ack itself is an ordinary interruptible TTS utterance that is not
  added to the LLM context (`append_to_context=False`), so the conversation history and the
  tool-call message order are untouched.

`QuickAckPolicy` is pure; `build_quick_ack` is the thin Pipecat processor (between the LLM
and the output guard/TTS).
"""
from __future__ import annotations

import random
from typing import Callable, Optional

from loguru import logger

ACKS = ("Okay.", "Got it.", "Mm-hm.", "Sure.", "Alright.")
SEED_EXTRACTION_MS = 1100.0   # production llm_tool p50 after LAT-002 (docs/penciled-comparison.md)
EMA_ALPHA = 0.5


class QuickAckPolicy:
    def __init__(self, *, enabled: bool, threshold_ms: float = 600.0,
                 allowed: Callable[[], bool] = lambda: True,
                 seed_ms: float = SEED_EXTRACTION_MS, rng: Optional[random.Random] = None):
        self.enabled, self.threshold_ms, self.allowed = enabled, threshold_ms, allowed
        self.expected_ms = seed_ms
        self._rng = rng or random.Random()
        self.last: Optional[str] = None
        self.user_speaking = False
        self.armed = False
        self.spoken: list[str] = []

    # -- frame-driven state --
    def user_started(self) -> None:
        self.user_speaking, self.armed = True, False

    def user_stopped(self) -> None:
        self.user_speaking, self.armed = False, True   # one ack per caller turn

    def interrupted(self) -> None:
        self.armed = False

    def observe_extraction(self, ms: Optional[float]) -> None:
        """Feed each turn's measured extraction time (TurnTimer's llm_tool)."""
        if ms is not None and ms > 0:
            self.expected_ms = EMA_ALPHA * ms + (1 - EMA_ALPHA) * self.expected_ms

    # -- decision --
    def on_llm_start(self) -> Optional[str]:
        """The LLM started on a caller turn: the ack to speak now, or None."""
        fire = (self.enabled and self.armed and not self.user_speaking
                and self.expected_ms > self.threshold_ms and self._allowed())
        self.armed = False
        if not fire:
            return None
        ack = self._rng.choice([a for a in ACKS if a != self.last])
        self.last = ack
        self.spoken.append(ack)
        return ack

    def _allowed(self) -> bool:
        try:
            return bool(self.allowed())
        except Exception as e:  # noqa: BLE001 - an ack is optional; never break the turn
            logger.warning(f"quick ack gate failed: {e}")
            return False


def build_quick_ack(policy: QuickAckPolicy, on_ack: Optional[Callable[[], None]] = None):
    """Pipecat processor. Sees the user aggregator's broadcast User*SpeakingFrames and the
    LLM's LLMFullResponseStartFrame (pushed the moment the extraction request starts)."""
    from pipecat.frames.frames import (InterruptionFrame, LLMFullResponseStartFrame, TTSSpeakFrame,
                                       UserStartedSpeakingFrame, UserStoppedSpeakingFrame)
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    class QuickAck(FrameProcessor):
        def __init__(self):
            super().__init__(name="quick_ack")

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, UserStartedSpeakingFrame):
                policy.user_started()
            elif isinstance(frame, UserStoppedSpeakingFrame):
                policy.user_stopped()
            elif isinstance(frame, InterruptionFrame):
                policy.interrupted()
            elif isinstance(frame, LLMFullResponseStartFrame) and direction == FrameDirection.DOWNSTREAM:
                ack = policy.on_llm_start()
                if ack:
                    if on_ack is not None:
                        on_ack()
                    await self.push_frame(TTSSpeakFrame(ack, append_to_context=False), direction)
            await self.push_frame(frame, direction)

    return QuickAck()
