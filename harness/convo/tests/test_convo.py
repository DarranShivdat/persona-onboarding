"""qa:convo — every convo-tier catalog case, in mock and replay mode, fully offline."""
import copy
import json
import socket

import pytest

from harness.convo import runner
from harness.convo.scoring import TurnTrace, check_state, on_track
from harness.evals.interface import ItemResult, LocalEvalBackend
from agent.brain.spec import load_spec

SPEC = load_spec()
CASES = runner.convo_cases()
RUNNABLE = [(c, b) for c, b in CASES if not b.get("pending")]


def _ids(cb):
    return cb[0]["id"]


@pytest.mark.parametrize("mode", runner.MODES)
@pytest.mark.parametrize("cb", RUNNABLE, ids=_ids)
def test_case(cb, mode):
    case, binding = cb
    with runner.NetworkGuard().active() as guard:
        r = runner.run_case(SPEC, case, binding, mode)
    assert r.status == "pass", r.failures
    assert r.scores == {"slot_correctness": 1.0, "on_track": 1.0}
    assert guard.attempts == []


def test_every_convo_case_is_bound_and_pending_has_a_reason():
    bindings = runner.load_bindings()
    for case, binding in CASES:
        assert case["id"] in bindings, case["id"]
        if binding.get("pending"):
            assert len(str(binding["pending"]).split()) >= 5, case["id"]
        else:
            assert runner.recording_path(case["id"]).exists(), f"{case['id']}: run `record --source mock`"
    stray = set(bindings) - {c["id"] for c, _ in CASES}
    assert not stray, f"bindings for non-convo cases: {stray}"


def test_catalog_expectations_are_all_checkable():
    """Every expected.state key in the catalog parses (unknown keys raise)."""
    st = runner.initial_state(SPEC, "x", {})
    for case in runner.load_catalog()["cases"]:
        exp = {k: v for k, v in case["expected"]["state"].items() if k != "call_lease_holder"}
        check_state(SPEC, exp, st, st)


def _mutated(case_id, fn):
    case, binding = next(cb for cb in CASES if cb[0]["id"] == case_id)
    binding = copy.deepcopy(binding)
    fn(binding)
    return runner.run_case(SPEC, case, binding, "mock")


def test_wrong_extraction_fails_the_case():
    # Without the injection intent the adversarial fixture would fill `need`: must fail.
    def drop_intent(b):
        for x in b["extractions"].values():
            x["intents"] = []
    r = _mutated("EC-26", drop_intent)
    assert r.status == "fail" and r.scores["slot_correctness"] < 1.0, r


def test_missing_fixture_fails_loudly_not_as_a_reask():
    r = _mutated("EC-18", lambda b: b.update(extractions={}))
    assert r.status == "fail" and "FixtureError" in r.failures[0]


def test_failed_extraction_without_retry_does_not_fill():
    r = _mutated("EC-07", lambda b: b.update(client_retry=False))
    assert r.status == "fail" and r.turns[0].extraction_failed and r.turns[0].reply


def test_replay_detects_drift(tmp_path):
    case, binding = next(cb for cb in CASES if cb[0]["id"] == "EC-18")
    lines = runner.recording_path("EC-18").read_text().splitlines()
    rows = [json.loads(l) for l in lines]
    rows[1]["utterance"] = "something else"
    (tmp_path / "EC-18.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    r = runner.run_case(SPEC, case, binding, "replay", recordings=tmp_path)
    assert r.status == "fail" and "drift" in r.failures[0]


def test_replay_missing_recording_fails(tmp_path):
    case, binding = RUNNABLE[0]
    assert runner.run_case(SPEC, case, binding, "replay", recordings=tmp_path).status == "fail"


def test_network_guard_blocks_and_counts():
    with runner.NetworkGuard().active() as g:
        with pytest.raises(OSError):
            socket.create_connection(("api.anthropic.com", 443), timeout=1)
        with pytest.raises(OSError):
            socket.getaddrinfo("api.anthropic.com", 443)
    assert len(g.attempts) == 2
    assert socket.getaddrinfo is not None and "deny" not in socket.getaddrinfo.__qualname__


def test_on_track_proxy():
    fwd = TurnTrace("user_name", "need", [], "need")
    back = TurnTrace("need", "user_name", ["agent_name"], "user_name")
    reask = TurnTrace("need", "need", ["need"], "need")
    change = TurnTrace("need", "user_name", [], "user_name", ["change_answer"])
    assert on_track(SPEC, [fwd, change]) == (1.0, [])
    score, notes = on_track(SPEC, [fwd, back, reask, change])
    assert score == 0.5 and len(notes) == 2


def test_run_name_and_compare(tmp_path):
    name = runner.run_name(SPEC)
    sha, flow, prompts = name.split("|")
    assert sha and flow == f"flow-v{SPEC.raw['version']}" and prompts.startswith("prompts-")
    b = LocalEvalBackend(tmp_path)
    b.record_run("a|flow-v1|prompts-x", "ds", [ItemResult("EC-1", None, {"slot_correctness": 1.0, "on_track": 1.0}),
                                               ItemResult("EC-2", None, {"slot_correctness": 1.0, "on_track": 1.0})], {})
    b.record_run("b|flow-v1|prompts-y", "ds", [ItemResult("EC-1", None, {"slot_correctness": 0.5, "on_track": 1.0}),
                                               ItemResult("EC-3", None, {"slot_correctness": 1.0, "on_track": 1.0})], {})
    d = b.compare("a|flow-v1|prompts-x", "b|flow-v1|prompts-y")
    assert d["EC-1"] == {"slot_correctness": -0.5, "on_track": 0.0}
    assert d["EC-2"]["slot_correctness"] == -1.0 and d["EC-3"]["slot_correctness"] == 1.0
    assert b.list_runs() == ["a|flow-v1|prompts-x", "b|flow-v1|prompts-y"]


def test_run_all_records_eval_run(tmp_path):
    b = LocalEvalBackend(tmp_path)
    out = runner.run_all("mock", backend=b)
    assert out["ok"] and out["metadata"]["network_attempts"] == []
    doc = b.load_run(out["run"])
    assert {r["case_id"] for r in doc["results"]} == {c["id"] for c, _ in RUNNABLE}
    assert set(doc["metadata"]["pending"]) == {c["id"] for c, b_ in CASES if b_.get("pending")}
