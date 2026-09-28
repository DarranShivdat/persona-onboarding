"""GMAIL-NOTNOW (live 2026-09-28): the Gmail card's "Not now" got a re-ask. It now defers Gmail at
once and graduates (Gmail = the deferred prompt on home), in text and on a call; the call says
"No problem, you can connect it later." with the closing summary, then ends."""
import asyncio

import pytest

from agent.api.llm import FakeLlm
from agent.brain.engine import Extraction, Turn, apply, is_gmail_defer
from agent.brain.state import SessionState, SlotValue
from agent.llm import templates as T

from test_voice_flows import SPEC  # noqa: E402


def _at_gmail(channel=None) -> SessionState:
    st = SessionState(session_id="nn", node="gmail", active_channel=channel)
    for k, v in {"agent_name": "Atlas", "user_name": "Darran", "need": "text messages"}.items():
        st.slots[k] = SlotValue(value=v, status="filled", source="text")
    return st


@pytest.mark.parametrize("words", ["Not now", "Skip for now", "not now.", "Maybe later", "no thanks"])
def test_text_not_now_defers_gmail_and_graduates(words):
    # Whatever the extractor made of it (even nothing), the button's words defer at once.
    r = apply(SPEC, _at_gmail(), Turn(channel="text", utterance=words, extraction=Extraction()))
    assert r.plan.graduate and r.state.graduated and r.plan.skipped == ["gmail"]
    assert r.state.slot("gmail").status == "skipped" and r.state.deferred_prompts == ["gmail"]
    assert not r.plan.explain_why
    reply = FakeLlm().phrase(spec=SPEC, state=r.state, plan=r.plan, channel="text")
    assert reply.startswith("No problem, you can connect it later. You're all set, Darran.")
    assert "connect Gmail from the main screen" in reply


def test_other_gmail_node_answers_are_untouched():
    for u in ["my email is darran@gmail.com", "what access does it get?", "now", "yes", "not sure"]:
        assert not is_gmail_defer(u), u
    r = apply(SPEC, _at_gmail(), Turn(channel="text", utterance="yes", extraction=Extraction(intents=["affirm"])))
    assert not r.plan.graduate


def test_not_now_elsewhere_is_not_a_gmail_skip():
    st = _at_gmail()
    st.node = "need"
    st.slots.pop("need")
    r = apply(SPEC, st, Turn(channel="text", utterance="Not now", extraction=Extraction()))
    assert r.state.slot("gmail").status != "skipped" and not r.plan.graduate


pipecat_flows = pytest.importorskip("pipecat_flows")


def test_voice_not_now_says_short_line_graduates_and_ends_the_call():
    from agent.voice.flows import LocalBrain, VoiceFlow
    from test_voice_flows import _args, _Ctx

    said = []

    async def graduated(line):          # the call session speaks this, then ends the call
        said.append(line)

    brain = LocalBrain(SPEC, _at_gmail("voice"))
    ctx = _Ctx()
    flow = VoiceFlow(SPEC, brain, context=ctx, on_graduated=graduated)

    async def go():
        ctx.messages.append({"role": "user", "content": "not now"})
        return await flow.handle_record_slots(_args("not now"), None)

    result, node = asyncio.run(go())
    st = asyncio.run(brain.current())
    assert result["graduated"] and node["name"] == "graduated" and st.slot("gmail").status == "skipped"
    assert len(said) == 1 and said[0].startswith(T.GMAIL_DEFERRED) and "You're all set, Darran." in said[0]


def test_tapping_not_now_during_a_call_speaks_the_line_and_ends_the_call():
    from agent.voice.flows import LocalBrain, VoiceFlow
    from test_voice_flows import _Ctx

    said = []

    async def graduated(line):
        said.append(line)

    r = apply(SPEC, _at_gmail("voice"), Turn(channel="text", utterance="Not now", extraction=Extraction()))
    flow = VoiceFlow(SPEC, LocalBrain(SPEC, r.state), context=_Ctx(), on_graduated=graduated)
    node = asyncio.run(flow.typed_turn(r.plan))
    assert node["name"] == "graduated"
    assert len(said) == 1 and said[0].startswith(T.GMAIL_DEFERRED) and "typed" not in said[0]


def test_typed_text_during_a_call_gets_no_typing_meta_comment():
    """TYPED-001: text typed during a call is acknowledged like speech ("Noted: ..."), never
    "I see you typed that in the chat"."""
    from agent.voice.flows import LocalBrain, VoiceFlow
    from test_voice_flows import _Ctx
    import agent.voice.flows as F

    st = _at_gmail("voice")
    st.node = "need"
    st.slots.pop("need")
    r = apply(SPEC, st, Turn(channel="text", utterance="text messages", extraction=Extraction(slots={"need": "text messages"})))
    node = asyncio.run(VoiceFlow(SPEC, LocalBrain(SPEC, r.state), context=_Ctx()).typed_turn(r.plan))
    said = node["pre_actions"][0]["text"]
    assert said.startswith("Noted: text messages.") and "typed" not in said.lower()
    assert not hasattr(F, "TYPED_ACK") and "typed" not in F.GMAIL_TYPED
