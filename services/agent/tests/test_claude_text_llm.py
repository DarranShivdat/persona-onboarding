"""Production text LLM (live test 2026-09-27): reaction-only phrasing, code-owned asks."""
from __future__ import annotations

from agent.api.claude_llm import ClaudeTurnLlm, clean_reaction
from agent.brain import engine
from agent.brain.engine import Extraction, Turn
from agent.brain.spec import load_spec
from agent.brain.state import SessionState

SPEC = load_spec()


class _Msgs:
    def __init__(self, text):
        self.text, self.calls = text, []

    def create(self, **kw):
        self.calls.append(kw)
        if isinstance(self.text, Exception):
            raise self.text
        return {"content": [{"type": "text", "text": self.text}], "usage": {}}


class _Client:
    def __init__(self, text):
        self.messages = _Msgs(text)


def _after_agent_name(llm):
    st = SessionState(session_id="t")
    st = engine.apply(SPEC, st, Turn(channel="text", event="open")).state
    r = engine.apply(SPEC, st, Turn(channel="text", utterance="Nova", extraction=Extraction(slots={"agent_name": "Nova"})))
    return r, llm.phrase(spec=SPEC, state=r.state, plan=r.plan, channel="text")


def test_model_question_is_dropped_and_code_appends_the_brains_offer():
    llm = ClaudeTurnLlm(SPEC, _Client("Nova, I like that. What's your name?"))
    r, text = _after_agent_name(llm)
    assert r.plan.offer_call
    assert text.startswith("Nova, I like that.")
    assert "your name" not in text and "Quick call?" in text


def test_self_intro_and_greeting_sentences_are_filtered():
    assert clean_reaction("Hey Darran. I'm Nova, here to help. Nice to meet you, Darran.") == "Nice to meet you, Darran."
    assert clean_reaction("Great! Love it.", max_sentences=1) == "Great."


def test_api_failure_falls_back_to_template_reaction_plus_ask():
    llm = ClaudeTurnLlm(SPEC, _Client(RuntimeError("boom")))
    _, text = _after_agent_name(llm)
    assert text.startswith("Nova. I like it.") and "Quick call?" in text


def test_voice_channel_never_calls_the_model():
    client = _Client("anything")
    llm = ClaudeTurnLlm(SPEC, client)
    st = SessionState(session_id="v")
    r = engine.apply(SPEC, st, Turn(channel="text", event="open"))
    llm.phrase(spec=SPEC, state=r.state, plan=r.plan, channel="voice")
    assert client.messages.calls == []


def test_privacy_answer_is_policy_template_not_paraphrase():
    client = _Client("Your data is totally encrypted everywhere.")
    llm = ClaudeTurnLlm(SPEC, client)
    st = SessionState(session_id="p")
    st = engine.apply(SPEC, st, Turn(channel="text", event="open")).state
    r = engine.apply(SPEC, st, Turn(channel="text", utterance="is my data safe?",
                                    extraction=Extraction(intents=["privacy_question"])))
    text = llm.phrase(spec=SPEC, state=r.state, plan=r.plan, channel="text")
    assert "encrypted everywhere" not in text and "Google sign-in" in text
    assert client.messages.calls == []


def test_docker_image_ships_the_product_facts_the_phraser_reads():
    # 2026-09-27 redeploy crash: /app/docs/product-facts.md was missing from the image.
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    assert "!docs/product-facts.md" in (root / ".dockerignore").read_text().splitlines()
    assert "COPY docs/product-facts.md /app/docs/product-facts.md" in (root / "infra/agent.Dockerfile").read_text()


def test_capability_pitch_is_filtered_mid_conversation():
    assert clean_reaction("Nice to meet you — I'm ready to help you get things done with your email, calendar, and everyday tasks.") == ""


def test_home_name_correction_is_deterministic_not_nice_to_meet_you():
    client = _Client("Nice to meet you, Darran.")
    llm = ClaudeTurnLlm(SPEC, client)
    st = SessionState(session_id="h")
    for turn in [Turn(channel="text", event="open"),
                 Turn(channel="text", utterance="Juno", extraction=Extraction(slots={"agent_name": "Juno"})),
                 Turn(channel="text", utterance="type", extraction=Extraction(intents=["decline_call"])),
                 Turn(channel="text", utterance="Darren", extraction=Extraction(slots={"user_name": "Darren"})),
                 Turn(channel="text", utterance="inbox", extraction=Extraction(slots={"need": "inbox zero"})),
                 Turn(channel="text", utterance="I'm done", extraction=Extraction(intents=["insist_graduate"]))]:
        st = engine.apply(SPEC, st, turn).state
    assert st.graduated
    r = engine.apply(SPEC, st, Turn(channel="text", utterance="my name is Darran not Darren",
                                    extraction=Extraction(slots={"user_name": "Darran"}, intents=["change_answer"])))
    text = llm.phrase(spec=SPEC, state=r.state, plan=r.plan, channel="text")
    assert r.state.slot("user_name").value == "Darran"
    assert "Darran it is" in text and "Nice to meet you" not in text
