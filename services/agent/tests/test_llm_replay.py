"""(b) Recorded-fixture replay: full turns (extract -> brain.apply -> phrase + guard)
driven by responses in tests/fixtures/llm/replay/. No network."""
import copy
import json
from pathlib import Path

import pytest

from agent.brain.spec import load_spec
from agent.brain.state import SessionState, SlotValue
from agent.llm import Extractor, Phraser, run_turn
from agent.llm.client import ReplayClient
from agent.llm.eval import evaluate, load_cases

FIX = Path(__file__).parent / "fixtures" / "llm"
SPEC = load_spec()


def _initial(fx) -> SessionState:
    st = SessionState(session_id="replay", node=fx["initial"]["node"])
    for name, value in fx["initial"].get("slots", {}).items():
        st.slots[name] = SlotValue(value=value, status="filled", source="text", validated_by=name)
    if fx["initial"]["node"] != "greet":
        st.call_offer_resolved = False
    return st


@pytest.mark.parametrize("path", sorted((FIX / "replay").glob("*.json")), ids=lambda p: p.stem)
def test_replay(path):
    fx = json.loads(path.read_text())
    channel = fx["channel"]
    state = _initial(fx)
    for i, t in enumerate(fx["turns"]):
        xc = ReplayClient([t["extract"]] if "extract" in t else [])
        pc = ReplayClient([t["phrase"]] if t.get("phrase") else [])
        before = copy.deepcopy(state)
        out = run_turn(SPEC, state, channel=channel, utterance=t.get("utterance", ""),
                       event=t.get("event"), oauth_verified="oauth_email" in t, oauth_email=t.get("oauth_email"),
                       extractor=Extractor(xc, SPEC, model="claude-haiku-4-5"),
                       phraser=Phraser(pc, SPEC, model="claude-haiku-4-5"))
        assert state == before, "run_turn must never mutate its input state"
        exp = t["expect"]
        new = out.result.state
        where = f"{path.stem} turn {i}"
        # Every recorded response was consumed, and nothing more was requested.
        assert not xc.messages._responses and not pc.messages._responses, where
        if "phrase" in t and t["phrase"] is None:
            assert pc.messages.calls == [], f"{where}: critical/template turn must not call the LLM"
        if "node" in exp:
            assert new.node == exp["node"], where
        if "graduated" in exp:
            assert new.graduated is exp["graduated"], where
        if "filled" in exp:
            assert sorted(s for s in SPEC.slots if new.filled(s)) == sorted(exp["filled"]), where
        for s in exp.get("candidate", []):
            assert new.slots[s].status == "candidate", where
        if exp.get("state_unchanged"):
            assert new == before, where
        for ui in exp.get("push_ui", []):
            assert ui in out.result.plan.push_ui, where
        if "reply" in exp:
            assert out.reply == exp["reply"], where
        for frag in exp.get("reply_contains", []):
            assert frag in out.reply, where
        if "dropped_reasons" in exp:
            assert [r for _, r in out.phrase.dropped] == exp["dropped_reasons"], where
        assert out.reply or out.result.plan.absorbed, f"{where}: never dead air"
        state = new


def test_eval_harness_scores_oracle_recordings():
    """The model eval replays recordings through the real parse path. An oracle built
    from the expectations must score 1.0; a model that invents slots must not."""
    cases = load_cases()
    names = list(SPEC.slots)

    def resp(slots, intents):
        return {"content": [{"type": "tool_use", "name": "record_slots", "input": {
            "slots": {n: slots.get(n) for n in names},
            "confidence": {n: 0.9 if slots.get(n) else 0.0 for n in names}, "intents": intents}}]}

    oracle = {c["id"]: {"latency_ms": 100 + i, "response": resp(c["expect"]["slots"], c["expect"]["intents"])}
              for i, c in enumerate(cases)}
    r = evaluate(SPEC, "oracle", cases, oracle)
    assert r["accuracy"] == 1.0 and r["recorded"] == len(cases), r["failures"]
    assert r["p50_ms"] is not None

    inventing = {k: {"response": resp({"agent_name": "Bob"}, [])} for k in oracle}
    assert evaluate(SPEC, "bad", cases, inventing)["accuracy"] < 0.5
    assert evaluate(SPEC, "none", cases, {})["recorded"] == 0
