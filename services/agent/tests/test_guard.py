"""GUARD-001: the model cannot steer the flow or call out-of-scope tools (offline).

(a) a function the current node does not expose (or any unknown function) is rejected:
    no brain turn, state unchanged, logged, the node's line re-spoken;
(b) transitions come only from the brain, whatever the LLM puts in its tool args;
(c) a new optional slot + node in a copy of flow.yaml runs with no engine change;
(d) the per-node acceptance table: agent_name never taken on a call, gmail never filled
    from extraction, intents not meaningful here dropped — each logged `rejected_extraction`;
(e) voice LLM settings + phrasing instruction + output guard on what it says.
"""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from agent.brain import engine
from agent.brain.engine import Extraction, Turn, apply
from agent.brain.spec import FlowSpec, SpecError, load_spec, validate
from agent.brain.state import SessionState, SlotValue
from agent.brain.validators import Validation
from agent.llm.schema import record_slots_tool

SPEC = load_spec()


def _at(node, channel="text", **slots):
    st = SessionState("g1", node=node, active_channel=channel, call_offer_resolved=True)
    vids = {"agent_name": "agent_name", "user_name": "person_name", "need": "need", "gmail": "gmail_oauth"}
    for k, v in slots.items():
        st.slots[k] = SlotValue(value=v, status="filled", source="text", validated_by=vids.get(k, "need"))
    return st


def _say(channel="text", intents=(), oauth=False, utterance="", **slots):
    return Turn(channel, utterance, Extraction(slots=slots, intents=list(intents)), oauth_verified=oauth)


def _rejections(res):
    return [e for e in res.events if e["type"] == "rejected_extraction"]


def _run(coro):
    return asyncio.run(coro)


# --- (d) per-node acceptance ------------------------------------------------------


def test_agent_name_extraction_on_voice_is_ignored_and_logged():
    st = _at("user_name", "voice", agent_name="Nova")
    res = apply(SPEC, st, _say("voice", agent_name="Evil Bot", need="triage my inbox every morning"))
    assert res.state.slot("agent_name").value == "Nova"            # never changed on a call
    assert res.state.filled("need")                                   # the rest of the turn still counts
    assert _rejections(res) == [{"type": "rejected_extraction", "node": "user_name", "channel": "voice",
                                 "reason": "slot_not_on_channel", "slot": "agent_name"}]
    fresh = apply(SPEC, _at("user_name", "voice"), _say("voice", agent_name="Nova"))
    assert "agent_name" not in fresh.state.slots or fresh.state.slot("agent_name").status == "empty"


def test_agent_name_on_voice_ignored_through_the_voice_handler():
    pytest.importorskip("pipecat_flows")
    from agent.voice.flows import LocalBrain, VoiceFlow

    brain = LocalBrain(SPEC, _at("user_name", "voice", agent_name="Nova"))
    flow = VoiceFlow(SPEC, brain)
    _run(flow.opening())
    _run(flow.handle_record_slots({"slots": {"agent_name": "Evil Bot"}, "confidence": {}, "intents": []}))
    st = _run(brain.current())
    assert st.slot("agent_name").value == "Nova"
    assert any(e["type"] == "rejected_extraction" and e.get("slot") == "agent_name" for e in brain.events)


def test_gmail_never_filled_from_extraction_even_if_a_validator_says_ok(monkeypatch):
    res = apply(SPEC, _at("gmail", need="x"), _say(gmail="sam@gmail.com"))
    assert res.state.slot("gmail").status == "candidate" and not _rejections(res)
    # Belt and braces: even a validator regression cannot let extraction fill gmail.
    monkeypatch.setitem(engine.VALIDATORS, "gmail_oauth", lambda raw, **kw: Validation("ok", raw))
    res = apply(SPEC, _at("gmail", need="x"), _say(gmail="sam@gmail.com"))
    assert res.state.slot("gmail").status == "candidate" and not res.state.filled("gmail")
    assert [r["reason"] for r in _rejections(res)] == ["extraction_cannot_fill"]
    ok = apply(SPEC, _at("gmail", need="x"), _say(gmail="sam@gmail.com", oauth=True))
    assert ok.state.filled("gmail") and not _rejections(ok)            # the OAuth callback still fills


@pytest.mark.parametrize("node,channel,intent", [
    ("need", "text", "decline_call"),        # only answers the call offer
    ("user_name", "voice", "unsure_need"),   # "not sure" only means something at need
    ("need", "voice", "accept_call"),        # already on the call
])
def test_intents_not_meaningful_here_are_dropped_with_an_event(node, channel, intent):
    res = apply(SPEC, _at(node, channel), _say(channel, intents=[intent]))
    assert {"type": "intent", "intent": intent} not in res.events
    assert _rejections(res) == [{"type": "rejected_extraction", "node": node, "channel": channel,
                                 "reason": "intent_not_accepted_here", "intent": intent}]


@pytest.mark.parametrize("node,channel,intent", [
    ("call_offer", "text", "decline_call"), ("need", "voice", "unsure_need"), ("user_name", "text", "accept_call"),
    ("need", "text", "prefer_typing"), ("need", "voice", "insist_graduate")])
def test_meaningful_intents_are_accepted(node, channel, intent):
    st = _at(node, channel, agent_name="Nova")
    st.call_offer_resolved = node != "call_offer"
    res = apply(SPEC, st, _say(channel, intents=[intent]))
    assert {"type": "intent", "intent": intent} in res.events and not _rejections(res)


def test_acceptance_table_is_spec_data_and_validated():
    assert SPEC.accepts_intent("decline_call", "call_offer", "text")
    assert not SPEC.accepts_intent("decline_call", "need", "voice")
    assert SPEC.accepts_intent("off_topic", "need", "voice")          # no rule: accepted anywhere
    assert SPEC.candidate_only("gmail") and not SPEC.candidate_only("need")
    assert not SPEC.accepts_slot("agent_name", "voice") and SPEC.accepts_slot("need", "voice")
    for bad in ({"bogus": {"nodes": ["need"]}}, {"unsure_need": {"nodes": ["nowhere"]}},
                {"accept_call": {"channels": ["fax"]}}):
        raw = copy.deepcopy(SPEC.raw)
        raw["acceptance"] = {"intents": bad}
        with pytest.raises(SpecError):
            validate(FlowSpec(raw))


# --- (a) out-of-node / unknown tool calls ------------------------------------------


def _opened_flow(**slots):
    pytest.importorskip("pipecat_flows")
    from agent.voice.flows import LocalBrain, VoiceFlow

    brain = LocalBrain(SPEC, _at("user_name", "voice", agent_name="Nova", **slots))
    flow = VoiceFlow(SPEC, brain)
    _run(flow.opening())
    return flow, brain


def test_out_of_node_tool_call_is_rejected_and_state_unchanged():
    flow, brain = _opened_flow()
    before = _run(brain.current())
    line = flow.last.line
    # push_gmail_connect is a registered tool, but no voice node exposes it (code pushes the card).
    handler = flow.guarded("push_gmail_connect", flow.handle_record_slots)
    result, node = _run(handler({"slots": {"gmail": "sam@gmail.com"}, "intents": ["insist_graduate"]}))
    assert _run(brain.current()) == before                                  # no brain turn at all
    assert result["rejected"] and result["reason"] == "not_on_node" and result["say"] == line
    assert node["name"] == "user_name" and line in node["task_messages"][0]["content"]  # line re-spoken
    assert flow.rejections == [{"type": "rejected_tool_call", "function": "push_gmail_connect",
                                "node": "user_name", "reason": "not_on_node"}]


def test_stale_record_slots_after_graduation_is_rejected():
    flow, brain = _opened_flow()
    handler = flow.record_slots_schema().handler      # registered earlier; Flows may keep it around
    _run(handler({"slots": {}, "confidence": {}, "intents": ["insist_graduate"]}))
    st = _run(brain.current())
    assert st.graduated and flow.node == "graduated"
    result, node = _run(handler({"slots": {"need": "anything"}, "confidence": {}, "intents": []}))
    assert result["rejected"] and node is None and _run(brain.current()) == st


def test_unknown_function_is_rejected_via_the_catch_all():
    flow, brain = _opened_flow()
    before = _run(brain.current())
    got = []

    async def cb(result, **_):
        got.append(result)

    _run(flow.handle_unknown_function(SimpleNamespace(function_name="graduate_user", arguments={},
                                                      result_callback=cb)))
    assert _run(brain.current()) == before
    assert got and got[0]["rejected"] and got[0]["reason"] == "unknown_function"
    assert flow.rejections[-1]["function"] == "graduate_user"


def test_call_session_registers_the_catch_all():
    pytest.importorskip("pipecat_flows")
    from unittest.mock import MagicMock

    from agent.voice.config import VoiceConfig
    from agent.voice.session import CallSession

    live = VoiceConfig.from_env({"DEEPGRAM_API_KEY": "dummy", "ANTHROPIC_API_KEY": "dummy"})
    s = CallSession(MagicMock(pc_id="pc"), live, call_id="c1")
    s._build()
    llm = s._flow_manager._llm
    assert llm._functions[None].handler == s.flow.handle_unknown_function


# --- (b) transitions only from the brain ---------------------------------------------


def test_llm_asking_for_another_node_is_ignored():
    """Tool args that 'ask' for a node, a graduation or a made-up intent change nothing the
    brain did not decide from the validated slots alone."""
    flow, brain = _opened_flow()
    base = _run(brain.current())
    hijack = {"slots": {"user_name": "S-A-M"}, "confidence": {"user_name": 0.99},
              "intents": ["goto_gmail", "graduate"], "node": "gmail", "next_node": "graduated",
              "graduated": True, "set_state": {"gmail": "filled"}}
    result, node = _run(flow.handle_record_slots(hijack))
    st = _run(brain.current())
    clean = apply(SPEC, base, _say("voice", user_name="S-A-M"))  # what the brain alone decides
    assert st.node == clean.state.node == node["name"] == result["node"] == flow.node == "need"
    assert not st.graduated and not st.filled("gmail")


def test_voice_task_never_offers_a_node_choice():
    flow, _ = _opened_flow()
    cfg = flow.node_config("need", flow.last)
    assert [f.name for f in cfg["functions"]] == ["record_slots"]
    text = cfg["task_messages"][0]["content"]
    for other in ("gmail", "graduated", "value_demo"):
        assert f"step: {other}" not in text


# --- (c) spec-driven: a new optional slot/node needs no engine change ---------------


def _with_timezone():
    raw = copy.deepcopy(SPEC.raw)
    raw["slots"]["timezone"] = {"description": "The user's time zone.", "required": False,
                                "channels": ["text", "voice"], "validator": "need",
                                "why": "So reminders land at the right time.", "confirm": "never",
                                "ask": "Which time zone are you in?"}
    raw["nodes"]["timezone"] = {"kind": "collect", "channel": "any", "slot": "timezone",
                                "goal": "Ask which time zone they are in.", "tools": ["record_slots"],
                                "next_when_filled": "gmail"}
    raw["nodes"]["need"]["next_when_filled"] = "timezone"
    for ch in ("text", "voice"):
        order = raw["ask_order"][ch]
        order.insert(order.index("need") + 1, "timezone")
    spec = FlowSpec(raw)
    validate(spec)
    return spec


def test_new_optional_slot_and_node_run_without_engine_change():
    spec = _with_timezone()
    tool = record_slots_tool(spec)
    assert "timezone" in tool["input_schema"]["properties"]["slots"]["required"]
    st = _at("need", "text", agent_name="Nova", user_name="Sam")
    r = apply(spec, st, _say(need="triage my inbox every morning"))
    assert r.state.node == "timezone" and r.plan.ask == "timezone"
    r = apply(spec, r.state, _say(timezone="Pacific time, Los Angeles"))
    assert r.state.filled("timezone") and r.state.node == "gmail"
    # out of order: a time zone given early fills and is skipped over later
    r = apply(spec, st, _say(need="triage my inbox every morning", timezone="Eastern time, New York"))
    assert r.state.node == "gmail"
    # optional: graduation never defers it
    g = apply(spec, st, _say(intents=["insist_graduate"]))
    assert "timezone" not in g.state.deferred_prompts


def test_new_node_builds_on_the_call_without_voice_change():
    pytest.importorskip("pipecat_flows")
    from agent.voice.flows import LocalBrain, VoiceFlow

    spec = _with_timezone()
    brain = LocalBrain(spec, _at("need", "voice", agent_name="Nova", user_name="Sam"))
    flow = VoiceFlow(spec, brain)
    _run(flow.opening())
    result, node = _run(flow.handle_record_slots({"slots": {"need": "triage my inbox every morning"},
                                                  "confidence": {}, "intents": []}))
    assert node["name"] == "timezone" and [f.name for f in node["functions"]] == ["record_slots"]
    assert "Which time zone are you in?" in flow.last.line
    assert flow.call_rejection("record_slots") is None


# --- (e) voice LLM settings, phrasing instruction, output guard ----------------------


def test_voice_llm_settings():
    pytest.importorskip("pipecat.services.anthropic.llm")
    from agent.voice.config import VoiceConfig
    from agent.voice.services import VOICE_LLM_MAX_TOKENS, VOICE_LLM_TEMPERATURE, build_llm

    cfg = VoiceConfig.from_env({"DEEPGRAM_API_KEY": "dummy", "ANTHROPIC_API_KEY": "dummy"})
    llm = build_llm(cfg)
    from agent.llm.extract import EXTRACT_MAX_TOKENS
    # Must fit a full record_slots call (a 120 cap truncated it in prod, 2026-09-28).
    assert VOICE_LLM_MAX_TOKENS >= EXTRACT_MAX_TOKENS and VOICE_LLM_TEMPERATURE == 0.3
    assert llm._settings.max_tokens == VOICE_LLM_MAX_TOKENS and llm._settings.temperature == 0.3


def test_voice_task_says_the_line_without_adding():
    flow, _ = _opened_flow()
    text = flow.node_config("user_name", flow.last)["task_messages"][0]["content"]
    assert "never add facts, questions or offers" in text and "own words" not in text


def test_speech_filter_drops_violations_and_never_leaves_silence():
    from agent.voice.speech_guard import SpeechFilter

    ctx = {"allowed": "What should I call you?", "gmail_connected": False, "fallback": "What should I call you?"}
    f = SpeechFilter(lambda: ctx)
    out = f.feed("Great, your Gmail is connected. ") + f.feed("What should I call you? It costs $5") + f.finish()
    assert out == ["What should I call you? "]
    assert [v["reason"] for v in f.violations] == ["claim:gmail_connected", "claim:price"]
    f.start()
    assert f.feed("I've already sent your emails. ") == [] and f.finish() == ["What should I call you?"]
    none = SpeechFilter(lambda: None)
    assert none.feed("anything at all") == ["anything at all"] and none.finish() == []


def test_speech_context_comes_from_the_brain_line():
    flow, _ = _opened_flow()
    ctx = flow.speech_context()
    assert ctx["fallback"] == flow.last.line and flow.last.line in ctx["allowed"]
    assert ctx["gmail_connected"] is False


def test_speech_guard_processor_in_a_pipeline():
    pytest.importorskip("pipecat")
    from pipecat.frames.frames import LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame
    from pipecat.tests.utils import run_test

    from agent.voice.speech_guard import build_speech_guard

    ctx = {"allowed": "Nice to meet you.", "gmail_connected": False, "fallback": "Nice to meet you."}
    frames = [LLMFullResponseStartFrame(), LLMTextFrame("Nice to meet you. "),
              LLMTextFrame("SOC 2 certified, by the way."), LLMFullResponseEndFrame()]
    down, _ = _run(run_test(build_speech_guard(lambda: ctx), frames_to_send=frames))
    spoken = "".join(f.text for f in down if isinstance(f, LLMTextFrame))
    assert spoken.strip() == "Nice to meet you."
