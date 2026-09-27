"""VOICE-003 / EC-10: per-node silence floor — nudge ≈7s, example ≈15s, then offer the
chat and end politely; never dead air, never a state change. Fake clock, no audio."""
import asyncio
from unittest.mock import MagicMock

import pytest

pytest.importorskip("pipecat")

from agent.brain.spec import TERMINAL_NODE, load_spec  # noqa: E402
from agent.voice.config import VoiceConfig  # noqa: E402
from agent.voice.silence import (  # noqa: E402
    EXAMPLE,
    NUDGE,
    PARK,
    PARK_LINE,
    SilenceFloor,
    SilencePolicy,
    run_silence_floor,
    silence_line,
)

SPEC = load_spec()


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _floor():
    clock = Clock()
    f = SilenceFloor(SilencePolicy(), clock=clock)
    f.start()
    return f, clock


def _ladder(f, clock, until, step=0.5):
    fired = []
    while clock.t < until:
        clock.t += step
        a = f.due()
        if a:
            fired.append((clock.t, a))
    return fired


def test_ladder_nudge_example_then_park():
    f, clock = _floor()
    assert _ladder(f, clock, 25) == [(7.0, NUDGE), (15.0, EXAMPLE), (23.0, PARK)]
    assert f.due() is None  # parked: nothing more


def test_caller_activity_resets_the_ladder():
    f, clock = _floor()
    assert _ladder(f, clock, 8) == [(7.0, NUDGE)]
    f.user_speaking(True)
    assert _ladder(f, clock, 30) == []            # nothing fires while the caller talks
    f.user_speaking(False)                        # t=30
    assert _ladder(f, clock, 38) == [(37.0, NUDGE)]


def test_bot_speech_pauses_and_the_question_end_starts_the_clock():
    f, clock = _floor()
    f.bot_speaking(True)
    assert _ladder(f, clock, 10) == []            # never nudge over the bot
    f.bot_speaking(False)                         # t=10: the question has played out
    assert _ladder(f, clock, 17.5) == [(17.0, NUDGE)]
    f.bot_speaking(True)                          # the nudge itself plays; it doesn't restart the clock
    clock.t = 27.0
    assert f.due() is None
    f.bot_speaking(False)
    assert f.due() == EXAMPLE


def test_every_node_has_spoken_lines():
    for node in SPEC.nodes:
        for action in (NUDGE, EXAMPLE, PARK):
            assert silence_line(SPEC, node, action).strip(), (node, action)
    assert "help with" in silence_line(SPEC, "need", NUDGE)
    assert "for example" in silence_line(SPEC, "need", EXAMPLE).lower()
    assert "Gmail" in silence_line(SPEC, "gmail", EXAMPLE)
    assert silence_line(SPEC, None, NUDGE)
    park = silence_line(SPEC, TERMINAL_NODE, PARK)
    assert park == PARK_LINE and "chat" in park and "saved" in park


def test_ec10_runner_speaks_twice_then_parks_without_touching_state():
    """25s of silence at node `need`: two spoken nudges, then the chat offer + polite end."""
    f, clock = _floor()
    spoken, parked = [], []

    async def sleep(secs):
        clock.t += secs

    async def speak(line):
        spoken.append((clock.t, line))

    async def park(line):
        parked.append((clock.t, line))

    asyncio.run(run_silence_floor(f, speak=speak, park=park, sleep=sleep,
                                  line=lambda a: silence_line(SPEC, "need", a)))
    assert [t for t, _ in spoken] == [7.0, 15.0] and parked[0][0] == 23.0
    assert "help with" in spoken[0][1] and "for example" in spoken[1][1].lower()
    assert "chat" in parked[0][1] and not f.armed


def test_policy_from_env():
    p = SilencePolicy.from_env({"PERSONA_SILENCE_NUDGE_S": "5"})
    assert (p.nudge_s, p.example_s, p.park_s) == (5.0, 15.0, 23.0)


def test_call_session_silence_floor_nudges_then_says_goodbye():
    """Wired into CallSession: node from the flow, nudges spoken, park = goodbye with
    reason `silence_timeout` (which the hand-off maps to hangup + chat resume)."""
    from agent.voice.session import CallSession

    s = CallSession(MagicMock(pc_id="pc"), VoiceConfig(), call_id="c1",
                    silence=SilencePolicy(nudge_s=0.05, example_s=0.1, park_s=0.15, poll_s=0.01))
    s.flow = MagicMock(last=MagicMock(plan=MagicMock(node="need")))
    spoken, goodbye = [], []

    async def speak(text):
        spoken.append(text)

    async def say_goodbye(text, reason):
        goodbye.append((text, reason))

    s.speak, s.say_goodbye = speak, say_goodbye
    s.silence.start()
    asyncio.run(asyncio.wait_for(s._silence_floor(), 2))
    assert len(spoken) == 2 and "help with" in spoken[0]
    assert goodbye == [(PARK_LINE, "silence_timeout")]


def test_call_session_silence_can_be_disabled():
    from agent.voice.session import CallSession

    assert CallSession(MagicMock(pc_id="pc"), VoiceConfig(), call_id="c1", silence=None).silence is None
