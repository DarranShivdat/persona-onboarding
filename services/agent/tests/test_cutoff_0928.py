"""CUTOFF-001 (live 2026-09-28): Atlas -> "Quick call?" -> start call opened with "Hey, we got
cut off." on a brand-new call. Root cause: the brain marks every call_started past the start
node as `resume` and its voice resume template was the dropped-call line; since NAME-004 the
chat stores the spoken voice line too. The brain's voice resume line is now the fresh-call
one; only VoiceFlow.opening(reconnect=True) (lease resumed in the grace window) says cut off."""
import asyncio

import pytest

pytest.importorskip("pipecat_flows")

from agent.api.llm import FakeLlm  # noqa: E402
from agent.brain.state import SessionState, SlotValue  # noqa: E402
from agent.llm import templates as T  # noqa: E402
from agent.voice.flows import VOICE_CONTINUE, LocalBrain, VoiceFlow, voice_line  # noqa: E402

from test_voice_flows import SPEC, _Ctx  # noqa: E402


def _after_call_offer() -> SessionState:
    st = SessionState(session_id="c1", node="user_name", active_channel=None)
    st.slots["agent_name"] = SlotValue(value="Atlas", status="filled", source="text")
    return st


def _opening(reconnect: bool) -> str:
    flow = VoiceFlow(SPEC, LocalBrain(SPEC, _after_call_offer()), context=_Ctx())
    node = asyncio.run(flow.opening(reconnect=reconnect))
    return node["pre_actions"][0]["text"]


def test_fresh_call_after_chat_never_says_cut_off():
    said = _opening(reconnect=False)
    assert "cut off" not in said.lower()
    assert said.startswith(VOICE_CONTINUE) and "call you" in said


def test_chat_transcript_of_a_fresh_call_never_says_cut_off():
    vt = asyncio.run(LocalBrain(SPEC, _after_call_offer()).event("call_started"))
    assert vt.plan.resume
    stored = FakeLlm().phrase(spec=SPEC, state=vt.state, plan=vt.plan, channel="voice")
    assert "cut off" not in stored.lower() and "cut off" not in voice_line(SPEC, vt.plan, vt.state).lower()


def test_real_reconnect_still_says_cut_off_once():
    said = _opening(reconnect=True)
    assert said.startswith(T.RESUME_CUT_OFF) and said.count("cut off") == 1
    assert VOICE_CONTINUE not in said and "call you" in said


def test_brand_new_session_call_is_the_greeting():
    flow = VoiceFlow(SPEC, LocalBrain(SPEC, SessionState(session_id="c2")), context=_Ctx())
    said = asyncio.run(flow.opening())["pre_actions"][0]["text"]
    assert "cut off" not in said.lower() and "left off" not in said.lower()
