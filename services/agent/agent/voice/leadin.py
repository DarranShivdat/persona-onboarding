"""Lead-in pad (CLIP-001, live 2026-09-28): the first syllable of a spoken line ("Thanks. So
that's Darran...") was clipped on the first call. The start of each bot utterance is the
fragile edge: the browser's playout/jitter buffer re-syncs as speech resumes after silence,
and a short one-word first sentence from the TTS starts on its first phoneme with no onset.
The fix keeps the wording and puts a few frames of silence in front of every utterance
(after TTSStartedFrame, before the first audio chunk), so the first sound always lands
after the edge. One pad per utterance; a pad of 0 turns it off (PERSONA_VOICE_LEADIN_MS).
"""
from __future__ import annotations

from typing import Optional

DEFAULT_LEADIN_MS = 120.0


def silence_bytes(ms: float, sample_rate: int, num_channels: int = 1) -> bytes:
    """16-bit PCM silence, whole samples only."""
    samples = int(sample_rate * max(ms, 0.0) / 1000.0)
    return b"\x00\x00" * samples * num_channels


def build_leadin_pad(ms: float, sample_rate: int):
    """Pipecat processor between the TTS and transport.output(). None when disabled."""
    if ms <= 0:
        return None
    from pipecat.frames.frames import InterruptionFrame, TTSAudioRawFrame, TTSStartedFrame, TTSStoppedFrame
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    class LeadInPad(FrameProcessor):
        def __init__(self):
            super().__init__(name="leadin_pad")
            self._pending: Optional[str] = None   # context_id of an utterance not padded yet
            self._armed = False
            self.pads = 0

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if direction == FrameDirection.DOWNSTREAM:
                if isinstance(frame, TTSStartedFrame):
                    self._armed, self._pending = True, getattr(frame, "context_id", None)
                elif isinstance(frame, (TTSStoppedFrame, InterruptionFrame)):
                    self._armed = False
                elif isinstance(frame, TTSAudioRawFrame) and self._armed:
                    self._armed = False
                    self.pads += 1
                    pad = TTSAudioRawFrame(silence_bytes(ms, frame.sample_rate or sample_rate, frame.num_channels or 1),
                                           frame.sample_rate or sample_rate, frame.num_channels or 1,
                                           context_id=getattr(frame, "context_id", None) or self._pending)
                    await self.push_frame(pad, direction)
            await self.push_frame(frame, direction)

    return LeadInPad()
