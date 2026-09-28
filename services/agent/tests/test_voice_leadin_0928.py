"""CLIP-001 (live 2026-09-28): the first syllable of "Thanks. So that's Darran..." was clipped.
Every spoken line now starts with a short silence pad (after TTSStartedFrame, before its first
audio), once per utterance; the words are unchanged. PERSONA_VOICE_LEADIN_MS=0 turns it off."""
import asyncio

import pytest

pytest.importorskip("pipecat")

from pipecat.frames.frames import TTSAudioRawFrame, TTSSpeakFrame, TTSStartedFrame  # noqa: E402
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.tests.utils import SleepFrame, run_test  # noqa: E402

from agent.voice.config import SAMPLE_RATE, VoiceConfig  # noqa: E402
from agent.voice.fakes import ToneTTS  # noqa: E402
from agent.voice.leadin import DEFAULT_LEADIN_MS, build_leadin_pad, silence_bytes  # noqa: E402


def _down(texts, ms=DEFAULT_LEADIN_MS):
    frames = []
    for t in texts:
        frames += [TTSSpeakFrame(t), SleepFrame(0.4)]
    procs = [ToneTTS(name="tone")] + ([build_leadin_pad(ms, SAMPLE_RATE)] if ms > 0 else [])
    down, _ = asyncio.run(run_test(Pipeline(procs), frames_to_send=frames))
    return down


def test_each_utterance_starts_with_one_silence_pad_before_speech():
    down = _down(["Thanks. So that's Darran, D-A-R-R-A-N?", "Nice to meet you, Darran."])
    pad = silence_bytes(DEFAULT_LEADIN_MS, SAMPLE_RATE)
    assert len(pad) == int(SAMPLE_RATE * 0.12) * 2
    starts = [i for i, f in enumerate(down) if isinstance(f, TTSStartedFrame)]
    assert len(starts) == 2
    for s in starts:
        audio = [f for f in down[s:] if isinstance(f, TTSAudioRawFrame)]
        assert audio[0].audio == pad                     # first audio after the start is the pad
        assert audio[1].audio != pad and any(audio[1].audio)   # then the real speech, unclipped
    assert sum(1 for f in down if isinstance(f, TTSAudioRawFrame) and f.audio == pad) == 2


def test_pad_off_and_config():
    assert build_leadin_pad(0, SAMPLE_RATE) is None
    assert VoiceConfig.from_env({}).leadin_ms == DEFAULT_LEADIN_MS
    assert VoiceConfig.from_env({"PERSONA_VOICE_LEADIN_MS": "0"}).leadin_ms == 0
    down = _down(["Thanks."], ms=0)
    assert not any(isinstance(f, TTSAudioRawFrame) and not any(f.audio) for f in down)
