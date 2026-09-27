"""Offline stand-ins for vendor services (no network, no keys).

`ToneTTS` synthesizes a short tone per text chunk. It backs `PERSONA_VOICE_FAKE_VENDORS=1`
(local transport/teardown proofs without vendor keys) and the failover unit tests,
where `fail_with` makes it report a provider error instead of audio.
"""
from __future__ import annotations

import math
import struct
from typing import AsyncGenerator, Optional

from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService

from .config import SAMPLE_RATE
from .failover import ErrorCategory


def tone(secs: float = 0.25, freq: int = 523, rate: int = SAMPLE_RATE) -> bytes:
    n = int(rate * secs)
    return struct.pack(
        f"<{n}h", *(int(8000 * min(1.0, i / 200, (n - i) / 200) * math.sin(2 * math.pi * freq * i / rate)) for i in range(n))
    )


class ToneTTS(TTSService):
    def __init__(self, *, name: Optional[str] = None, freq: int = 523, fail_with: Optional[ErrorCategory] = None,
                 speak_after_error: bool = False, **kwargs):
        super().__init__(name=name, sample_rate=SAMPLE_RATE, push_start_frame=True, push_stop_frames=True,
                         stop_frame_timeout_s=0.3, settings=TTSSettings(model="tone", voice=str(freq), language=None),
                         **kwargs)
        self.freq = freq
        self.fail_with = fail_with
        self.speak_after_error = speak_after_error
        self.spoken: list[str] = []

    def can_generate_metrics(self) -> bool:
        return False

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        if self.fail_with is not None:
            error = ErrorFrame(error=f"{self.name} synthetic failure", processor=self)
            error.category = self.fail_with  # set post-init: older Pipecat has no such field
            await self.push_error_frame(error)
            if not self.speak_after_error:
                yield None
                return
        self.spoken.append(text)
        yield TTSAudioRawFrame(audio=tone(freq=self.freq), sample_rate=SAMPLE_RATE, num_channels=1, context_id=context_id)

