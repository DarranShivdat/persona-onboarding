"""NAME-004 (live 2026-09-28 ~1:25pm PT, voice call shown in chat, agent c029e5d):
"Just to confirm, Darren? (yes/no)" -> "No. It's d a r r a n." -> "... Darran? (yes/no)" ->
"Yes." -> the same read-back again, forever.

Root cause: on "Yes." the voice extractor returned {user_name: "Darran", intents: [affirm]}.
The re-extracted pending value went through the validator again (voice read-back), became a
"new" candidate, and marked the slot as touched, so the affirm was never applied. The chat also
showed a text-style "(yes/no)" line (the service's voice reply came from the text template)
while the call spoke the natural read-back.
"""
import asyncio

import pytest

from agent.api.llm import FakeLlm
from agent.brain.engine import Extraction, Turn, apply, is_explicit_yes
from agent.brain.spec import load_spec
from agent.brain.state import SessionState, SlotValue
from agent.llm.testing import tool_response
from agent.voice.flows import LocalBrain, VoiceFlow, voice_line

SPEC = load_spec()


def say(text, channel="voice", intents=(), conf=None, **slots):
    return Turn(channel, text, Extraction(slots=slots, confidences=conf or {}, intents=list(intents)))


def at(channel="voice"):
    st = SessionState("y1", node="user_name", active_channel=channel, call_offer_resolved=True)
    st.slots["agent_name"] = SlotValue("Nova", "filled", "text", validated_by="agent_name")
    return st


def name(r):
    return r.state.slots["user_name"]


def test_live_transcript_voice_yes_settles_the_read_back():
    r = apply(SPEC, at(), say("Darren", user_name="Darren"))
    assert voice_line(SPEC, r.plan, r.state) == "Nice to meet you. Did I get that right: Darren, D-A-R-R-E-N?"
    r = apply(SPEC, r.state, say("No. It's d a r r a n.", intents=["deny"], user_name="Darran"))
    line = voice_line(SPEC, r.plan, r.state)
    assert line == "Thanks. So that's Darran, D-A-R-R-A-N?" and "(yes/no)" not in line
    # exactly what the live extractor returned for "Yes."
    r = apply(SPEC, r.state, say("Yes.", intents=["affirm"], user_name="Darran"))
    assert name(r).status == "filled" and name(r).value == "Darran" and not name(r).low_confidence
    assert r.state.node == "need" and r.plan.confirm is None
    assert voice_line(SPEC, r.plan, r.state).startswith("Perfect, thanks Darran.")


def test_live_transcript_through_the_voice_handler_never_says_yes_no():
    def args(slots=None, intents=()):
        return tool_response(SPEC, slots, intents)["content"][0]["input"]

    class Ctx:
        messages: list = []

        def get_messages(self):
            return self.messages

    async def go():
        ctx = Ctx()
        ctx.messages = []
        flow = VoiceFlow(SPEC, LocalBrain(SPEC, at()), context=ctx)
        await flow.opening()
        said = []
        for u, a in [("Darren", args({"user_name": "Darren"})),
                     ("No. It's d a r r a n.", args({"user_name": "Darran"}, ["deny"])),
                     ("Yes.", args({"user_name": "Darran"}, ["affirm"]))]:
            ctx.messages.append({"role": "user", "content": u})
            r, _ = await flow.handle_record_slots(a, None)
            said.append(r["say"])
        return said, await flow.brain.current()

    said, st = asyncio.run(go())
    assert all("(yes/no)" not in s for s in said)
    assert said[1].endswith("Darran, D-A-R-R-A-N?") and said[2].startswith("Perfect, thanks Darran.")
    assert st.slots["user_name"].status == "filled" and st.node == "need"


def test_live_transcript_text_yes_settles_the_read_back():
    r = apply(SPEC, at("text"), say("uh darren i guess", "text", conf={"user_name": 0.5}, user_name="Darren"))
    assert r.plan.confirm == "user_name"
    r = apply(SPEC, r.state, say("No. It's Darran, spelled differently", "text", intents=["deny"],
                                 conf={"user_name": 0.5}, user_name="Darran"))
    assert r.plan.confirm == "user_name" and name(r).value == "Darran"
    r = apply(SPEC, r.state, say("Yes.", "text", intents=["affirm"], user_name="Darran"))
    assert name(r).status == "filled" and name(r).value == "Darran" and r.state.node == "need"


def test_fake_llm_voice_reply_is_the_spoken_line_not_yes_no():
    r = apply(SPEC, at(), say("Darren", user_name="Darren"))
    text = FakeLlm().phrase(spec=SPEC, state=r.state, plan=r.plan, channel="voice")
    assert text == voice_line(SPEC, r.plan, r.state) and "(yes/no)" not in text


YES = ["yes", "Yes.", "YES!", "yeah", "Yeah.", "yep", "Yup", "correct", "Correct.", "That's right.",
       "that’s right", "yes, that's right", "yes that is correct", "yeah that's it", "right", "exactly",
       "Mm-hmm", "uh huh"]


@pytest.mark.parametrize("utterance", YES)
@pytest.mark.parametrize("channel", ["voice", "text"])
def test_explicit_yes_variants_settle_even_if_the_extractor_missed_affirm(utterance, channel):
    st = at(channel)
    st.slots["user_name"] = SlotValue("Darran", "candidate", channel, needs_confirm=True, confirm_attempts=2)
    r = apply(SPEC, st, say(utterance, channel))                       # no intents at all
    assert name(r).status == "filled" and name(r).value == "Darran", utterance
    assert r.state.node == "need"
    r = apply(SPEC, st, say(utterance, channel, intents=["affirm"], user_name="Darran"))
    assert name(r).status == "filled" and r.state.node == "need", utterance


@pytest.mark.parametrize("utterance", ["no", "yes but it's Darren", "Darran", "what?", "hmm", "yesterday", ""])
def test_not_an_explicit_yes(utterance):
    assert not is_explicit_yes(utterance)


def test_no_infinite_read_back_without_a_yes():
    st = at()
    r = apply(SPEC, st, say("Darren", user_name="Darren"))
    for i in range(10):
        if r.state.slots["user_name"].status == "filled":
            break
        r = apply(SPEC, r.state, say("what's that?", intents=["off_topic"]))
    assert name(r).status == "filled" and name(r).low_confidence and i <= 4
    assert r.state.node == "need"


def test_no_infinite_loop_even_with_a_yes_and_a_new_value_each_time():
    """The cap still applies when every turn carries a yes (with a different name)."""
    st = at()
    r = apply(SPEC, st, say("Darren", user_name="Darren"))
    names = ["Darran", "Daren", "Darrun", "Darin", "Doran", "Duran", "Deran", "Daran"]
    for i, v in enumerate(names):
        if name(r).status == "filled":
            break
        r = apply(SPEC, r.state, say(f"yes {v}", intents=["affirm"], user_name=v))
    assert name(r).status == "filled" and i <= 4
