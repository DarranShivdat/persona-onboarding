"""NAME-GREET (live 2026-09-28): the call introduced the assistant as "Persona" although the
user had named it (the call header shows "Atlas"). Every self-introduction uses the filled
agent_name slot; "Persona" only when no name is filled. Product answers about Persona stay."""
import asyncio
import re

import pytest

pytest.importorskip("pipecat_flows")

from agent.api.llm import FakeLlm  # noqa: E402
from agent.brain.state import SessionState, SlotValue  # noqa: E402
from agent.llm import templates as T  # noqa: E402
from agent.voice import session as voice_session  # noqa: E402
from agent.voice import services as voice_services  # noqa: E402
from agent.voice.flows import LocalBrain, VoiceFlow, role_message, voice_greet  # noqa: E402

from test_voice_flows import SPEC, _Ctx  # noqa: E402


def _named(name="Atlas", node="user_name") -> SessionState:
    st = SessionState(session_id="n1", node=node)
    st.slots["agent_name"] = SlotValue(value=name, status="filled", source="text")
    return st


def _opening(st, reconnect=False):
    flow = VoiceFlow(SPEC, LocalBrain(SPEC, st), context=_Ctx())
    node = asyncio.run(flow.opening(reconnect=reconnect))
    return node, node["pre_actions"][0]["text"]


def test_fresh_call_introduces_the_assistant_by_its_name():
    node, said = _opening(_named())
    assert said.startswith("Hi, it's Atlas! Let's pick up where we left off in the chat.")
    assert "Persona" not in said
    assert "You are Atlas" in node["role_message"] and "Persona" not in node["role_message"]


def test_chat_transcript_uses_the_name_too():
    vt = asyncio.run(LocalBrain(SPEC, _named("Juno")).event("call_started"))
    stored = FakeLlm().phrase(spec=SPEC, state=vt.state, plan=vt.plan, channel="voice")
    assert stored.startswith("Hi, it's Juno!") and "Persona" not in stored


def test_name_is_cleaned_and_reconnect_still_says_cut_off():
    _, said = _opening(_named("Atlas."), reconnect=True)
    assert said.startswith(T.RESUME_CUT_OFF) and "Persona" not in said and "it's Atlas" not in said
    assert T.agent_name(_named("  Nova! ")) == "Nova"


def test_fallback_only_when_no_name():
    empty = SessionState(session_id="n2")
    assert T.agent_name(empty) == "Persona" and T.agent_name(None) == "Persona"
    assert voice_greet(empty).startswith("Hi, it's Persona!")
    assert voice_greet(_named("Atlas")).startswith("Hi, it's Atlas!")
    assert role_message(None).startswith("You are Persona") and role_message(_named()).startswith("You are Atlas")


def test_no_hardcoded_persona_self_reference_in_speech():
    self_ref = re.compile(r"\b(it's|I'm|I am|this is|you are) Persona\b", re.IGNORECASE)
    for line in (voice_session.GREETING, voice_services.VOICE_SYSTEM_PROMPT, T.RESUME["voice"], T.GREET):
        assert not self_ref.search(line), line
    # Product answers about the company keep the name.
    assert T.APPROVED_ANSWERS["what_is_persona"][1].startswith("Persona's a personal AI assistant")
