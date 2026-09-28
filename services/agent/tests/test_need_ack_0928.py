"""HONEST-001 (live test 2026-09-28, voice): "Text messages." got "Good one, I can help with
that." Hard guardrail, no inference: there is NO capability classifier and no list of what
the assistant can do. The LLM only extracts the need; code acknowledges every need with ONE
fixed template ("Noted: {need}.") and bridges to Gmail with fixed copy that states
permissions only. No model-written text reaches the user at the need step.
"""
from __future__ import annotations

import pytest

from agent.api.claude_llm import ClaudeTurnLlm
from agent.api.llm import template_phrase
from agent.brain import engine
from agent.brain.engine import Extraction, Turn
from agent.brain.spec import load_spec
from agent.brain.state import SessionState, SlotValue
from agent.llm import templates as T
from agent.llm.guard import guard
from agent.llm.phrase import Phraser
from agent.voice.flows import voice_line

SPEC = load_spec()
NEEDS = ["text messages", "book reservations for me", "clean up my inbox"]
BANNED = ["i can help", "can help with that", "help with that", "can't do", "cannot do", "good one",
          "i can do", "i'll handle", "first job", "is starting with", "take off your plate"]
WHY = SPEC.slots["gmail"]["why"]
TOKEN = "<<NEED>>"


class _Msgs:
    def __init__(self, text):
        self.text, self.calls = text, []

    def create(self, **kw):
        self.calls.append(kw)
        return {"content": [{"type": "text", "text": self.text}], "usage": {}}


class _Client:
    def __init__(self, text):
        self.messages = _Msgs(text)


def _at_need(channel: str) -> SessionState:
    st = SessionState(session_id="h", node="need", active_channel=channel, call_offer_resolved=True)
    st.slots["agent_name"] = SlotValue("Jarvis", "filled", "text", validated_by="agent_name")
    st.slots["user_name"] = SlotValue("Darran", "filled", channel, validated_by="person_name")
    return st


def _need_turn(need: str, channel: str):
    return engine.apply(SPEC, _at_need(channel), Turn(channel, need.capitalize() + ".",
                                                      Extraction(slots={"need": need.capitalize() + "."})))


def _shape(text: str, need: str) -> str:
    return text.replace(need, TOKEN)


def _clean(text: str) -> None:
    low = text.lower()
    assert not [b for b in BANNED if b in low], text


def test_gmail_pitch_states_permissions_only():
    assert WHY == ("To get started, let's connect your Gmail. Connecting it will let your assistant read, "
                   "organize, and send email with your OK.")
    _clean(WHY)


@pytest.mark.parametrize("channel", ["text", "voice"])
def test_need_ack_is_identical_in_structure_for_every_need(channel):
    lines = {}
    for need in NEEDS:
        r = _need_turn(need, channel)
        assert r.plan.acknowledge == ["need"] and r.state.node == "gmail"
        line = voice_line(SPEC, r.plan, r.state) if channel == "voice" else template_phrase(SPEC, r.state, r.plan)
        _clean(line)
        assert line.startswith(f"Noted: {need}. To get started, let's connect your Gmail.")
        lines[need] = _shape(line, need)
    assert len(set(lines.values())) == 1, lines
    ask = T.ask_line("gmail", channel)
    assert next(iter(lines.values())) == f"Noted: {TOKEN}. {WHY} {ask}"


def test_claude_text_path_never_lets_model_text_through_at_the_need_step():
    """Even a model that would say "Good one, I can help with that" is never called here."""
    shapes = set()
    for need in NEEDS:
        client = _Client("Good one, I can help with that! Connecting Gmail lets me handle it.")
        llm = ClaudeTurnLlm(SPEC, client)
        r = _need_turn(need, "text")
        text = llm.phrase(spec=SPEC, state=r.state, plan=r.plan, channel="text")
        assert client.messages.calls == [], "no model call at the need step"
        _clean(text)
        shapes.add(_shape(text, need))
    assert shapes == {f"Noted: {TOKEN}. {WHY} {T.ask_line('gmail', 'text')}"}


def test_claude_text_path_need_ask_is_fixed_copy_too():
    client = _Client("Darran, great name! I can help with loads of things.")
    llm = ClaudeTurnLlm(SPEC, client)
    st = _at_need("text")
    st.node = "user_name"
    st.slots["user_name"] = SlotValue()
    r = engine.apply(SPEC, st, Turn("text", "Darran", Extraction(slots={"user_name": "Darran"})))
    assert r.plan.ask == "need"
    text = llm.phrase(spec=SPEC, state=r.state, plan=r.plan, channel="text")
    assert client.messages.calls == [] and text == "Nice to meet you, Darran. " + T.ask_line("need", "text")


def test_phraser_llm_path_is_bypassed_at_the_need_step():
    for need in NEEDS:
        client = _Client("Good one, I can help with that.")
        ph = Phraser(client, SPEC, model="claude-haiku-4-5")
        r = _need_turn(need, "voice")
        out = ph.phrase(r.plan, r.state, "voice")
        assert client.messages.calls == [] and out.source == "template"
        _clean(out.text)
        assert out.text.startswith(f"Noted: {need}.")


def test_a_need_change_is_fixed_copy_and_home_need_edit_skips_the_model():
    assert T.ack_for("need", "Book reservations for me", changed=True) == "Okay, noted: book reservations for me."
    st = _at_need("text")
    st.slots["need"] = SlotValue("text messages", "filled", "text", validated_by="need")
    st.node, st.graduated = "graduated", True
    client = _Client("Great choice, I can help with that!")
    llm = ClaudeTurnLlm(SPEC, client)
    r = engine.apply(SPEC, st, Turn("text", "I also want help booking reservations",
                                    Extraction(slots={"need": "booking reservations"}, intents=["change_answer"])))
    text = llm.phrase(spec=SPEC, state=r.state, plan=r.plan, channel="text")
    _clean(text)
    assert "Great choice" not in text


def test_closing_line_and_home_copy_make_no_capability_claim():
    st = _at_need("voice")
    st.slots["need"] = SlotValue("Text messages", "filled", "voice", validated_by="need")
    line = T.graduation_summary(st, ["gmail"])
    assert line.startswith("You're all set, Darran. Jarvis has noted what you'd like help with: text messages.")
    _clean(line)
    for text in [*T.HOME_REPLY.values(), *T.RESPOND.values(), *T.ACK_SLOT.values(), *T.CHANGED_SLOT.values(),
                 T.NAME_KEEP, T.SUGGEST]:
        _clean(text)


def test_fixed_need_copy_passes_the_output_guard():
    """The ack + permissions pitch is also acceptable to the guard's approved-facts check."""
    for need in NEEDS:
        r = _need_turn(need, "voice")
        line = f"Noted: {need}. {WHY}"
        assert voice_line(SPEC, r.plan, r.state).startswith(line)
        allowed = Phraser(None, SPEC).allowed_corpus(r.state)
        assert guard(line, allowed=allowed, gmail_connected=False).text == line


@pytest.mark.parametrize("line", [
    "Good one, I can help with that.", "I can't do that yet.", "Nova can definitely help you with that.",
    "I'll take care of it.", "Your assistant can handle that for you.", "We can do this together!",
])
def test_guard_drops_model_capability_claims_either_way(line):
    assert guard(line, allowed=line).text == ""


def test_guard_keeps_neutral_lines():
    for line in ["Nova. I like it.", "Nice to meet you, Darran.", "Love that name.",
                 "You can do that from the main screen."]:
        assert guard(line).text == line
