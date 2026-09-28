"""(c) Output guard table: bad lines are dropped with a reason, good lines survive."""
import pytest

from agent.brain.spec import load_spec
from agent.llm.guard import check, guard
from agent.llm.phrase import Phraser

SPEC = load_spec()


def _allowed(values=("Nova", "Sam", "get to inbox zero")):
    from agent.brain.state import SessionState, SlotValue
    st = SessionState(session_id="g", slots={f"s{i}": SlotValue(value=v) for i, v in enumerate(values)})
    return Phraser(None, SPEC, model="claude-haiku-4-5").allowed_corpus(st)


BAD = [
    # tools / functions
    ("Let me call record_slots to save that.", "tool"),
    ("I'll use push_gmail_connect now.", "tool"),
    ("One sec, making a tool call.", "tool"),
    # JSON / code
    ('{"agent_name": "Nova"}', "json"),
    ('"intents": ["affirm"]', "json"),
    ("Your user_name is saved.", "json"),
    ("```json", "json"),
    # stage directions / labels / markup
    ("*smiles warmly*", "stage_direction"),
    ("(pause) So, what should I call you?", "stage_direction"),
    ("[laughs] Love that name.", "stage_direction"),
    ("Assistant: What should I call you?", "stage_direction"),
    ("<thinking>ask for name</thinking>", "stage_direction"),
    # unapproved claims
    ("Persona is SOC 2 certified, so you're safe.", "claim:certification"),
    ("It's HIPAA compliant too.", "claim:certification"),
    ("Persona is free for the first month.", "claim:price"),
    ("It only costs $20 a month.", "claim:price"),
    ("We never sell your data.", "claim:selling"),
    ("We keep your voice recordings for a year.", "claim:retention"),
    ("We don't retain your call audio.", "claim:retention"),
    ("We don't share your data with anyone.", "claim:sharing"),
    ("Persona Band ships next month.", "claim:dates"),
    ("We just raised a big round from investors.", "claim:funding"),
    ("Your data is 100% secure, guaranteed.", "claim:guarantee"),
    ("It's end-to-end encrypted.", "claim:end_to_end"),
    ("Setup takes 90 seconds.", "claim:number"),
    ("Your Gmail is now connected!", "claim:gmail_connected"),
    ("I've connected your Gmail.", "claim:gmail_connected"),
    ("I can see you have a lot of unread emails in your inbox.", "claim:fabricated_action"),
    ("I've already drafted a reply to your boss.", "claim:fabricated_action"),
]

GOOD = [
    "Nova it is!",
    "Nice to meet you, Sam.",
    "What's one thing you'd most like help with first?",
    "Want to finish setup on a quick call? Typing works just as well.",
    "Gmail connects through Google sign-in, so I never see your password.",
    "Your Google tokens are stored encrypted, and you can disconnect any time.",
    "Nothing gets sent without your OK.",
    "Once it's connected, Nova can start helping you get to inbox zero.",
    "Tap the Connect Gmail button whenever you're ready.",
    "If you disconnect, the stored tokens are deleted.",
    "Sorry, I can only do English for now.",
    "I'll leave that one. Let's keep going with setup.",
]


@pytest.mark.parametrize("line,reason", BAD, ids=[b[0][:40] for b in BAD])
def test_bad_lines_dropped(line, reason):
    assert check(line, allowed=_allowed()) == reason
    assert guard(line, allowed=_allowed()).text == ""


@pytest.mark.parametrize("line", GOOD)
def test_good_lines_kept(line):
    assert check(line, allowed=_allowed()) is None, line
    assert guard(line, allowed=_allowed()).text == line


def test_mixed_output_keeps_only_clean_sentences():
    raw = '{"slots": {}}\nGot it, Sam! *nods*\nPersona is SOC 2 certified. What should we call your assistant?'
    g = guard(raw, allowed=_allowed())
    assert g.text == "Got it, Sam! What should we call your assistant?"
    assert [r for _, r in g.dropped] == ["json", "stage_direction", "claim:certification"]


def test_gmail_connected_allowed_when_state_says_so():
    assert check("Your Gmail is now connected!", allowed=_allowed(), gmail_connected=True) is None


def test_numbers_allowed_when_in_state():
    assert check("Got it: 3 meetings a day.", allowed=_allowed(("3 meetings a day",))) is None


def test_length_cap():
    g = guard("One. Two. Three. Four.", allowed="")
    assert g.kept == ["One.", "Two.", "Three."] and g.dropped == [("Four.", "too_long")]
