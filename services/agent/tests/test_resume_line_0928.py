"""RESUME-003 (live 2026-09-28): "Hi, it's Atlas! Let's pick up where we left off in the chat."
lagged mid-line on TTS. The fresh-call line is now one short sentence into the next ask, spoken
as ONE TTS utterance (a single tts_say pre-action = one TTSSpeakFrame = one TTS request)."""
import asyncio

import pytest

pytest.importorskip("pipecat_flows")

from agent.api.llm import FakeLlm  # noqa: E402
from agent.brain.state import SessionState, SlotValue  # noqa: E402
from agent.llm import templates as T  # noqa: E402
from agent.voice.flows import LocalBrain, VoiceFlow  # noqa: E402

from test_voice_flows import SPEC, _Ctx  # noqa: E402


def _state(node, **slots) -> SessionState:
    st = SessionState(session_id="r1", node=node)
    for k, v in {"agent_name": "Atlas", **slots}.items():
        st.slots[k] = SlotValue(value=v, status="filled", source="text")
    return st


def _open(st, reconnect=False):
    node = asyncio.run(VoiceFlow(SPEC, LocalBrain(SPEC, st), context=_Ctx()).opening(reconnect=reconnect))
    says = [a for a in node.get("pre_actions", []) if a["type"] == "tts_say"]
    assert len(says) == 1                        # one utterance: greeting + ask never split
    return says[0]["text"]


def test_fresh_call_at_user_name_is_one_short_sentence_into_the_ask():
    said = _open(_state("user_name"))
    assert said == "Hi, it's Atlas! So, what should I call you?"
    assert "left off" not in said and "chat" not in said


def test_fresh_call_at_need_flows_into_that_ask():
    said = _open(_state("need", user_name="Darran"))
    assert said.startswith("Hi, it's Atlas! So, ") and "left off" not in said
    ask = said[len("Hi, it's Atlas! So, "):]
    assert ask[:1].islower() and ask.endswith("?")


def test_chat_shows_the_same_line():
    vt = asyncio.run(LocalBrain(SPEC, _state("user_name")).event("call_started"))
    assert FakeLlm().phrase(spec=SPEC, state=vt.state, plan=vt.plan, channel="voice") == \
        "Hi, it's Atlas! So, what should I call you?"


def test_reconnect_keeps_the_cut_off_line():
    assert _open(_state("user_name"), reconnect=True) == f"{T.RESUME_CUT_OFF} What should I call you?"
