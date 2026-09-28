"""HOME-002 / SIL-002: regressions from Darran's live test on Mon 2026-09-28 (~12:30pm PT).

Replays the graduation-screen transcript he hit and the voice Gmail step where the silence
floor nudged at ~7s/~15s and hung up at ~23s while he was in the Google OAuth popup.
"""
import pytest

from agent.api.llm import template_phrase
from agent.brain.engine import Extraction
from agent.brain.home import pending, spelling_suggestion
from agent.brain.spec import load_spec

from test_api_edit import graduated, pushes
from test_api_sessions import SECRET, client, dsn, llm, new_session, store, turn  # noqa: F401 - fixtures
from test_engine import apply, at, event, say

SPEC = load_spec()


def replies(store, sid):
    return [p["data"]["text"] for p in pushes(store, sid) if p["type"] == "transcript" and p["data"]["role"] == "assistant"]


def slots(client, sid, auth):
    st = client.get(f"/v1/sessions/{sid}", headers=auth).json()
    st = st.get("state", st)
    return {k: v.get("value") for k, v in st["slots"].items()}


# ---- the closing line: once, from the current confirmed values ------------------------------

def test_closing_line_uses_current_confirmed_values():
    st = at("gmail", agent_name="jarvis", user_name="Darran", need="connect email")
    r = apply(SPEC, st, say("later", intents=["refuse_slot"]))
    line = template_phrase(SPEC, r.state, r.plan)
    assert line.startswith("You're all set, Darran. jarvis's first job: connect email.")
    assert "Juno" not in line and "Darren" not in line and "mom" not in line


def test_call_ending_after_graduation_does_not_repeat_the_closing():
    grad = apply(SPEC, at("gmail", agent_name="jarvis", user_name="Darran", need="connect email"),
                 say("later", intents=["refuse_slot"])).state
    for ev in ("call_ended", "open"):
        r = apply(SPEC, grad, event(ev))
        assert r.plan.absorbed and template_phrase(SPEC, r.state, r.plan) == ""


# ---- the graduation-screen transcript ----------------------------------------------------------

def test_live_transcript_replay_on_home(client, llm, store):
    sid, auth = graduated(client, llm)
    n_closing = sum("all set" in t for t in replies(store, sid))
    assert n_closing == 1

    # 2) "my name is Darrran not darren": extracted, "not darren" dropped, spelling checked
    turn(client, sid, auth, "my name is Darrran not darren")
    r = replies(store, sid)[-1]
    assert "did you mean Darran (D-A-R-R-A-N)?" in r and "all set" not in r
    assert slots(client, sid, auth)["user_name"] == "Ada"          # nothing saved until confirmed
    turn(client, sid, auth, "yes")
    assert replies(store, sid)[-1] == "Thanks, Darran it is."
    assert slots(client, sid, auth)["user_name"] == "Darran"

    # 4) "change my name": even if the model re-extracts every slot from context, it asks and waits
    before = slots(client, sid, auth)
    llm.push(Extraction(slots={"user_name": "Darran", "agent_name": "jarvis", "need": "connect email"},
                        intents=["change_answer"]))
    turn(client, sid, auth, "change my name")
    assert replies(store, sid)[-1] == "Sure. What should I call you?"
    assert slots(client, sid, auth) == before
    turn(client, sid, auth, "Sam")
    assert replies(store, sid)[-1] == "Thanks, Sam it is."
    assert slots(client, sid, auth)["user_name"] == "Sam" and slots(client, sid, auth)["agent_name"] == before["agent_name"]

    # 5) off-topic: a brief natural answer + redirect, still scoped
    turn(client, sid, auth, "whats 5 + 5")
    assert replies(store, sid)[-1].startswith("5 + 5 is 10.")
    llm.push(Extraction(intents=["off_topic"]))
    turn(client, sid, auth, "who won the world cup?")
    assert replies(store, sid)[-1].startswith("That's outside what I can help with")

    # 1) nothing after graduation repeats the closing
    assert sum("all set" in t for t in replies(store, sid)) == n_closing


def test_yes_to_the_spelling_check_confirms_even_when_unchanged():
    # live replay on 64b0652: the name already was Darran, so "yes" fell through to the generic line
    grad = apply(SPEC, at("gmail", agent_name="jarvis", user_name="Darran", need="connect email"),
                 say("later", intents=["refuse_slot"])).state
    r = apply(SPEC, grad, say("my name is Darrran not darren"))
    r2 = apply(SPEC, r.state, say("yes"))
    assert template_phrase(SPEC, r2.state, r2.plan) == "Thanks, Darran it is."


def test_spelling_check_accepts_the_typed_spelling_when_insisted():
    grad = apply(SPEC, at("gmail", agent_name="jarvis", user_name="Darren", need="connect email"),
                 say("later", intents=["refuse_slot"])).state
    r = apply(SPEC, grad, say("my name is Darrran not darren"))
    assert pending(r.state) == ["spell", "user_name", "Darrran", "Darran"] and r.state.slots["user_name"].value == "Darren"
    r2 = apply(SPEC, r.state, say("no, Darrran"))
    assert r2.state.slots["user_name"].value == "Darrran" and pending(r2.state) is None
    r3 = apply(SPEC, r.state, say("no"))
    assert pending(r3.state) == ["ask", "user_name"] and "home_ask_user_name" in r3.plan.respond_to
    r4 = apply(SPEC, r.state, say("never mind"))
    assert r4.state.slots["user_name"].value == "Darren" and pending(r4.state) is None


@pytest.mark.parametrize("text,slot", [("change my name", "user_name"), ("can I change my name?", "user_name"),
                                       ("change your name", "agent_name"), ("update what I need help with", "need")])
def test_value_less_edit_asks_and_waits(text, slot):
    grad = apply(SPEC, at("gmail", agent_name="jarvis", user_name="Darran", need="connect email"),
                 say("later", intents=["refuse_slot"])).state
    r = apply(SPEC, grad, say(text))
    assert pending(r.state) == ["ask", slot] and r.plan.respond_to == [f"home_ask_{slot}"]
    assert not (r.plan.changed or r.plan.acknowledge)


def test_spelling_suggestion():
    assert spelling_suggestion("Darrran") == "Darran" and spelling_suggestion("Darran") is None
    assert spelling_suggestion("Anna") is None


# ---- voice: silence floor at the Gmail step ------------------------------------------------------

def test_gmail_step_waits_quietly_while_oauth_runs():
    pytest.importorskip("pipecat")
    from agent.voice.silence import EXAMPLE, GMAIL_CHECKIN, GMAIL_WAIT, NUDGE, PARK, SilenceFloor, SilencePolicy, silence_line

    class Clock:
        t = 0.0

        def __call__(self):
            return self.t

    clock = Clock()
    f = SilenceFloor(SilencePolicy(), clock=clock)
    f.start()
    seen = []
    for t in range(0, 200):
        clock.t = float(t)
        a = f.due("gmail")
        if a:
            seen.append((t, a))
    # live call: nudges at ~7s/~15s and a hang-up at ~23s. Now: one "take your time", one late check-in.
    assert seen == [(10, NUDGE), (75, EXAMPLE), (180, PARK)]
    assert silence_line(SPEC, "gmail", NUDGE) == GMAIL_WAIT and "wait" in GMAIL_WAIT
    assert silence_line(SPEC, "gmail", EXAMPLE) == GMAIL_CHECKIN
    # other nodes keep the short ladder
    g = SilenceFloor(SilencePolicy(), clock=clock)
    clock.t = 0.0
    g.start()
    clock.t = 7.0
    assert g.due("need") == NUDGE
