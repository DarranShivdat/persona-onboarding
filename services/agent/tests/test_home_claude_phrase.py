"""GRAD-001: Claude text phrasing on home turns — reaction-only for edits, templated policy text."""
from agent.api.claude_llm import ClaudeTurnLlm
from agent.brain.engine import Extraction, Turn, apply
from agent.brain.spec import load_spec
from agent.llm import templates as T

from test_claude_text_llm import _Client
from test_home_brain import home_state

SPEC = load_spec()


def phrase(llm, text):
    r = apply(SPEC, home_state(), Turn("text", text, Extraction()))
    return r, llm.phrase(spec=SPEC, state=r.state, plan=r.plan, channel="text")


def test_correction_edit_is_templated_without_calling_the_model():
    client = _Client("Nice to meet you, Nova.")
    llm = ClaudeTurnLlm(SPEC, client)
    _, text = phrase(llm, "rename yourself Nova")
    assert text == "Okay, Nova it is."
    assert client.messages.calls == []


def test_policy_answers_never_call_the_model():
    client = _Client("I just sent that email for you.")
    llm = ClaudeTurnLlm(SPEC, client)
    _, text = phrase(llm, "send an email to Tom")
    assert text.startswith(T.HOME_REPLY["home_task"]) and "sent that" not in text
    assert client.messages.calls == []


def test_model_failure_falls_back_to_template():
    llm = ClaudeTurnLlm(SPEC, _Client(RuntimeError("down")))
    _, text = phrase(llm, "call me Darran")
    assert text == "Thanks, Darran it is."
