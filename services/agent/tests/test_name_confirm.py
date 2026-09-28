"""NAME-001: getting the user's name right (live test 2026-09-27: "Darran" heard as "Darren").

Voice always reads the name back (spelled); a "no" re-asks, then asks for letters; the
third miss keeps the best candidate (filled, low_confidence). Text confirms only unusual
names — typed text is authoritative. Corrections at any later point update user_name.
"""
import asyncio

import pytest

from agent.brain.engine import Extraction, Turn, apply
from agent.brain.spec import load_spec
from agent.brain.state import SessionState, SlotValue
from agent.brain.validators import assemble_spelling, person_name
from agent.llm import templates as T
from agent.llm.testing import tool_response
from agent.voice.flows import LocalBrain, VoiceFlow, voice_line

SPEC = load_spec()
POLICY = SPEC.slots["user_name"]["confirm_policy"]


def say(text="", channel="voice", intents=(), conf=None, **slots):
    return Turn(channel, text, Extraction(slots=slots, confidences=conf or {}, intents=list(intents)))


def at(node, channel="voice", **slots):
    st = SessionState("n1", node=node, active_channel=channel, call_offer_resolved=True)
    st.slots["agent_name"] = SlotValue("Nova", "filled", "text", validated_by="agent_name")
    for k, v in slots.items():
        st.slots[k] = SlotValue(v, "filled", channel, validated_by="person_name" if k == "user_name" else k)
    return st


def run(st, *turns):
    res = None
    for t in turns:
        res = apply(SPEC, st, t)
        st = res.state
    return res


def name(res):
    return res.state.slots["user_name"]


YES = say("yes", intents=["affirm"])
NO = say("no", intents=["deny"])


# --- spelling assembler -------------------------------------------------------


@pytest.mark.parametrize("utterance,expected", [
    ("D A R R A N", "Darran"),
    ("D-A-R-R-A-N", "Darran"),
    ("d, a, r, r, a, n.", "Darran"),
    ("D A double R A N", "Darran"),
    ("D as in David, A, double R, A, N", "Darran"),
    ("D for David, A, R, R, A, N", "Darran"),
    ("no, it's D A R R A N", "Darran"),
    ("it's a D A R R A N", "Darran"),
    ("Darran, D-A-R-R-A-N", "Darran"),
    ("delta alpha romeo romeo alpha november", "Darran"),
    ("S as in Sam, I, O, B, H as in Harry, A, N", "Siobhan"),
    ("I'm Sam", None),
    ("my name is J Smith", None),
    ("", None),
])
def test_assemble_spelling(utterance, expected):
    assert assemble_spelling(utterance) == expected


# --- validator policy ----------------------------------------------------------


@pytest.mark.parametrize("raw,kw,outcome,reason", [
    ("Darran", {"channel": "text", "utterance": "Darran"}, "ok", None),
    ("Darran", {"channel": "text", "utterance": "darran", "confidence": 0.97}, "ok", None),
    ("Darran", {"channel": "text", "utterance": "Darran", "confidence": 0.6}, "confirm", "low_confidence"),
    ("Mary Ann Smith", {"channel": "text", "utterance": "Mary Ann Smith"}, "confirm", "many_words"),
    ("Call Me Maybe", {"channel": "text", "utterance": "call me maybe"}, "confirm", "many_words"),
    ("Just Dan", {"channel": "text", "utterance": "just dan"}, "confirm", "sentence"),
    ("Darran", {"channel": "text", "utterance": "Daran"}, "confirm", "not_as_typed"),
    ("Dar4n", {"channel": "text", "utterance": "Dar4n"}, "confirm", "unusual_charset"),
    ("D-A-R-R-A-N", {"channel": "text", "utterance": "D-A-R-R-A-N"}, "ok", None),
    ("Darren", {"channel": "voice", "confidence": 0.95}, "confirm", "voice_read_back"),
    ("D-A-R-R-A-N", {"channel": "voice"}, "ok", None),     # spelling IS the read-back
])
def test_person_name_confirm_policy(raw, kw, outcome, reason):
    r = person_name(raw, confirm_policy=POLICY, **kw)
    assert (r.outcome, r.reason) == (outcome, reason)


# --- brain: voice always reads the name back --------------------------------------


def test_voice_always_confirms_user_name_then_yes_fills():
    r = apply(SPEC, at("user_name"), say("I'm Darran", conf={"user_name": 0.95}, user_name="Darren"))
    assert r.plan.confirm == "user_name" and r.state.node == "user_name" and name(r).status == "candidate"
    assert name(r).confirm_attempts == 1 and r.plan.ask is None
    r = apply(SPEC, r.state, YES)
    assert name(r).status == "filled" and name(r).value == "Darren" and not name(r).low_confidence
    assert r.state.node == "need" and r.plan.acknowledge == ["user_name"]


def test_voice_no_with_spelled_correction_fills_the_spelling():
    r = run(at("user_name"), say("Darran", user_name="Darren"),
            say("no, it's D A R R A N", intents=["deny"], user_name="Darren"))
    assert name(r).status == "filled" and name(r).value == "Darran" and r.state.node == "need"


def test_voice_no_reasks_then_asks_to_spell_then_caps_at_three():
    st = at("user_name")
    r = run(st, say("Darran", user_name="Darren"), NO)
    assert name(r).status == "empty" and r.plan.ask == "user_name" and r.plan.denied == ["user_name"]
    assert r.plan.spell is None and r.state.node_attempts.get("user_name", 0) == 0
    r = run(r.state, say("no, Darran", intents=["deny"], user_name="Darren"))   # 2nd try: read back again
    assert r.plan.confirm == "user_name" and name(r).confirm_attempts == 2
    r = apply(SPEC, r.state, NO)
    assert r.plan.spell == "user_name" and r.plan.ask == "user_name"             # now: letter by letter
    r = apply(SPEC, r.state, say("Darren", user_name="Darren"))                 # didn't spell after all
    assert r.plan.confirm == "user_name" and name(r).confirm_attempts == 3
    r = apply(SPEC, r.state, NO)                                                 # cap: keep it, move on
    assert name(r).status == "filled" and name(r).value == "Darren" and name(r).low_confidence
    assert r.state.node == "need" and r.plan.acknowledge == ["user_name"] and r.plan.confirm is None
    assert any(e.get("low_confidence") for e in r.events)


def test_voice_new_candidate_past_the_cap_is_kept_not_reread():
    st = at("user_name")
    st.slots["user_name"] = SlotValue("Darren", "candidate", "voice", needs_confirm=True, confirm_attempts=3)
    r = apply(SPEC, st, say("no, Darrun", intents=["deny"], user_name="Darrun"))
    assert name(r).status == "filled" and name(r).value == "Darrun" and name(r).low_confidence
    assert r.state.node == "need"


def test_voice_spelling_with_as_in_and_double_is_assembled_from_the_transcript():
    st = at("user_name")
    r = apply(SPEC, st, say("D as in David, A, double R, A, N", user_name="D as in David A double R A N"))
    assert name(r).status == "filled" and name(r).value == "Darran"


# --- brain: text confirms only unusual names -----------------------------------------


def test_text_plain_typed_name_never_confirms():
    r = apply(SPEC, at("user_name", "text"), say("Darran", "text", conf={"user_name": 0.95}, user_name="Darran"))
    assert name(r).status == "filled" and r.plan.confirm is None and r.state.node == "need"


def test_text_low_confidence_name_confirms_then_yes():
    r = apply(SPEC, at("user_name", "text"), say("uh darran i guess", "text", conf={"user_name": 0.5},
                                                 user_name="Darran"))
    assert r.plan.confirm == "user_name" and name(r).status == "candidate"
    assert T.confirm_line("user_name", "Darran", "text") == "Just checking, should I call you Darran?"
    r = apply(SPEC, r.state, say("yes", "text", intents=["affirm"]))
    assert name(r).status == "filled" and r.state.node == "need"


def test_text_no_just_reasks_never_spell():
    r = run(at("user_name", "text"), say("Dar4n", "text", user_name="Dar4n"), say("no", "text", intents=["deny"]))
    assert name(r).status == "empty" and r.plan.ask == "user_name" and r.plan.spell is None


# --- corrections at any later point ------------------------------------------------------


def test_text_correction_later_updates_name_and_ack_says_it():
    st = at("need", "text", user_name="Darren")
    r = apply(SPEC, st, say("actually it's spelled Darran", "text", intents=["change_answer"], user_name="Darran"))
    assert name(r).value == "Darran" and r.plan.changed == ["user_name"] and r.state.node == "need"
    assert T.ack_for("user_name", "Darran", changed=True) == "Thanks, Darran it is."


def test_voice_spelled_correction_later_updates_name():
    st = at("gmail", user_name="Darren")
    st.slots["need"] = SlotValue("inbox", "filled", "voice", validated_by="need")
    r = apply(SPEC, st, say("actually it's D A R R A N", intents=["change_answer"], user_name="Darran"))
    assert name(r).value == "Darran" and name(r).status == "filled" and r.plan.changed == ["user_name"]


def test_voice_unspelled_correction_is_read_back_first():
    st = at("need", user_name="Darren")
    r = apply(SPEC, st, say("actually it's Darran", intents=["change_answer"], user_name="Darran"))
    assert name(r).status == "candidate" and r.plan.confirm == "user_name" and r.state.node == "user_name"
    assert r.plan.changed == [] and voice_line(SPEC, r.plan, r.state) == "Thanks. So that's Darran, D-A-R-R-A-N?"
    r = apply(SPEC, r.state, YES)
    assert name(r).value == "Darran" and name(r).status == "filled" and r.state.node == "need"
    assert voice_line(SPEC, r.plan, r.state).startswith("Perfect, thanks Darran.")


def test_you_got_my_name_wrong_reopens_the_name():
    st = at("need", user_name="Darren")
    r = apply(SPEC, st, say("you've got my name wrong", intents=["change_answer"]))
    assert name(r).status == "empty" and r.state.node == "user_name" and r.plan.ask == "user_name"
    assert r.plan.spell == "user_name"
    r = apply(SPEC, r.state, say("D A R R A N", user_name="DARRAN"))
    assert name(r).value == "Darran" and name(r).status == "filled" and r.state.node == "need"


def test_text_you_got_my_name_wrong_reasks_without_spelling():
    r = apply(SPEC, at("need", "text", user_name="Darren"),
              say("you've got my name wrong", "text", intents=["change_answer"]))
    assert r.state.node == "user_name" and r.plan.ask == "user_name" and r.plan.spell is None


# --- voice lines ----------------------------------------------------------------------


def test_voice_lines_are_templated_short_and_spell_only_the_name():
    st = at("user_name")
    r1 = apply(SPEC, st, say("Darran", user_name="Darren"))
    l1 = voice_line(SPEC, r1.plan, r1.state)
    assert l1 == "Nice to meet you. Did I get that right: Darren, D-A-R-R-E-N?"
    r2 = apply(SPEC, r1.state, NO)
    assert voice_line(SPEC, r2.plan, r2.state) == T.NAME_REASK
    r3 = apply(SPEC, r2.state, say("Darran", user_name="Darren"))
    assert voice_line(SPEC, r3.plan, r3.state) == "Thanks. So that's Darren, D-A-R-R-E-N?"
    r4 = apply(SPEC, r3.state, NO)
    assert voice_line(SPEC, r4.plan, r4.state) == T.NAME_SPELL_ASK
    r5 = apply(SPEC, r4.state, say("Darren", user_name="Darren"))
    r6 = apply(SPEC, r5.state, NO)
    l6 = voice_line(SPEC, r6.plan, r6.state)
    assert l6.startswith("No worries, I'll go with Darren for now.") and "help with" in l6
    for line in (l1, T.NAME_REASK, T.NAME_SPELL_ASK, T.NAME_KEEP.format(v="Darren")):
        assert len(line.split()) < 25
    assert "Alpha" not in l1 and "as in" not in l1


def _args(slots=None, intents=()):
    return tool_response(SPEC, slots, intents)["content"][0]["input"]


class _Ctx:
    def __init__(self):
        self.messages = []

    def get_messages(self):
        return self.messages


def test_voice_flow_darren_then_spelled_no_fills_darran():
    """Live-test regression: "Darran" heard as "Darren" -> read back -> "no, it's D A R R A N"."""
    st = at("user_name")
    ctx = _Ctx()
    brain = LocalBrain(SPEC, st)
    flow = VoiceFlow(SPEC, brain, context=ctx)
    flow.note_stt_confidence(0.95)

    async def go():
        ctx.messages.append({"role": "user", "content": "Darren"})
        r1, n1 = await flow.handle_record_slots(_args({"user_name": "Darren"}), None)
        ctx.messages.append({"role": "user", "content": "no, it's D A R R A N"})
        r2, n2 = await flow.handle_record_slots(_args({"user_name": "Darren"}, ["deny"]), None)
        return r1, n1, r2, n2, await brain.current()

    r1, n1, r2, n2, final = asyncio.run(go())
    assert r1["node"] == "user_name" and n1["name"] == "user_name"
    assert "Darren, D-A-R-R-E-N?" in r1["say"]
    assert final.slots["user_name"].value == "Darran" and final.slots["user_name"].status == "filled"
    assert final.slots["user_name"].confidence == 0.95          # STT confidence passed through
    assert r2["node"] == "need" and n2["name"] == "need"
    assert r2["say"].startswith("Perfect, thanks Darran.")
    # the graduation screen/summary uses the confirmed spelling
    assert "Darran" in T.graduation_summary(final, [])
