"""TTS failover (Cartesia -> Deepgram) through Pipecat's ServiceSwitcher, with fakes.

No network, no keys: `ToneTTS` stands in for both vendors. Runs the real switcher and
frame flow via `pipecat.tests.utils.run_test`.
"""
import asyncio

import pytest

pytest.importorskip("pipecat")

from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame, TTSSpeakFrame  # noqa: E402
from pipecat.pipeline.service_switcher import ServiceSwitcher  # noqa: E402
from pipecat.tests.utils import SleepFrame, run_test  # noqa: E402

from agent.voice.fakes import ToneTTS  # noqa: E402
from agent.voice.failover import ErrorCategory, TtsFailoverStrategy, build_tts_switcher  # noqa: E402


def _run(proc, texts):
    # Utterances on a call are seconds apart; the gap lets the switcher see the
    # primary's error before the next utterance arrives.
    frames = []
    for t in texts:
        frames += [TTSSpeakFrame(t), SleepFrame(0.4)]
    return asyncio.run(run_test(proc, frames_to_send=frames))


def _audio(frames):
    return [f for f in frames if isinstance(f, TTSAudioRawFrame)]


@pytest.mark.parametrize("category", [ErrorCategory.UNKNOWN, ErrorCategory.CONNECTIVITY, ErrorCategory.SERVER,
                                      ErrorCategory.RATE_LIMIT, ErrorCategory.AUTHENTICATION])
def test_primary_error_fails_over_to_backup_and_keeps_speaking(category):
    primary = ToneTTS(name="cartesia", fail_with=category)
    backup = ToneTTS(name="deepgram", freq=440)
    switched = []

    async def on_switched(svc):
        switched.append(svc.name)

    switcher = build_tts_switcher([primary, backup], on_switched=on_switched)
    assert isinstance(switcher, ServiceSwitcher)
    down, up = _run(switcher, ["Hi, I'm your Persona.", "What should I call you?"])

    assert switched == ["deepgram"]
    assert switcher.strategy.active_service is backup
    assert not switcher.strategy.is_healthy(primary) and switcher.strategy.is_healthy(backup)
    # The failed utterance is lost (documented risk); every later one is spoken by the backup.
    assert backup.spoken == ["What should I call you?"]
    assert len(_audio(down)) >= 1
    # A successful failover absorbs the error: nothing upstream would tear the call down.
    assert not [f for f in up if isinstance(f, ErrorFrame)]


def test_healthy_primary_never_switches():
    primary, backup = ToneTTS(name="cartesia"), ToneTTS(name="deepgram")
    switcher = build_tts_switcher([primary, backup])
    down, _ = _run(switcher, ["One.", "Two."])
    assert switcher.strategy.active_service is primary
    assert primary.spoken == ["One.", "Two."] and backup.spoken == []
    assert len(_audio(down)) == 2


def test_application_errors_do_not_switch():
    # App-code errors say nothing about the provider; it still produced audio.
    primary = ToneTTS(name="cartesia", fail_with=ErrorCategory.APPLICATION, speak_after_error=True)
    backup = ToneTTS(name="deepgram")
    switcher = build_tts_switcher([primary, backup])
    _run(switcher, ["One."])
    assert switcher.strategy.active_service is primary and switcher.strategy.is_healthy(primary)


def test_both_failing_reports_error_upstream_without_crashing():
    primary = ToneTTS(name="cartesia", fail_with=ErrorCategory.SERVER)
    backup = ToneTTS(name="deepgram", fail_with=ErrorCategory.SERVER)
    switcher = build_tts_switcher([primary, backup])
    _, up = _run(switcher, ["One.", "Two."])
    assert not switcher.is_usable
    assert [f for f in up if isinstance(f, ErrorFrame)], "exhausted failover must surface an error"


def test_silent_primary_fails_over():
    # Pipecat 1.4 has no silent-context error (it arrived with pipecat.utils.errors).
    pytest.importorskip("pipecat.utils.errors", reason="this Pipecat does not report silent TTS (residual risk)")
    # A provider that "succeeds" with no audio is dead air: Pipecat reports it and we switch.
    class Silent(ToneTTS):
        async def run_tts(self, text, context_id):
            yield None

    primary, backup = Silent(name="cartesia"), ToneTTS(name="deepgram")
    switcher = build_tts_switcher([primary, backup])
    _run(switcher, ["One.", "Two.", "Three."])
    assert switcher.strategy.active_service is backup and backup.spoken


def test_single_service_is_returned_unwrapped():
    only = ToneTTS(name="deepgram")
    assert build_tts_switcher([only]) is only


def test_strategy_type_is_ours():
    switcher = build_tts_switcher([ToneTTS(name="a"), ToneTTS(name="b")])
    assert isinstance(switcher.strategy, TtsFailoverStrategy)
