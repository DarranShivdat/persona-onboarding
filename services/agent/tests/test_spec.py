"""Structural invariants of packages/flow/flow.yaml (qa:fast + qa:flow)."""
import copy

import pytest

from agent.brain.spec import FlowSpec, SpecError, load_spec, validate


@pytest.fixture(scope="module")
def spec():
    return load_spec()


def test_spec_loads_and_validates(spec):
    assert spec.raw["id"] == "persona-onboarding"


def test_four_required_slots(spec):
    assert set(spec.slots) == {"agent_name", "user_name", "need", "gmail"}
    assert all(s["required"] for s in spec.slots.values())


def test_agent_name_is_text_only_and_not_on_call(spec):
    assert spec.slots["agent_name"]["channels"] == ["text"]
    assert "agent_name" not in spec.ask_order("voice")
    assert spec.ask_order("voice") == ["user_name", "need", "gmail"]


def test_graduation_reachable_from_every_non_terminal_node(spec):
    for nid, n in spec.nodes.items():
        if n["kind"] != "terminal":
            assert "graduated" in spec.successors(nid), nid


def test_gmail_filled_only_by_oauth(spec):
    assert spec.slots["gmail"]["validator"] == "gmail_oauth"


def test_every_collect_node_runs_all_slot_extraction(spec):
    for nid, n in spec.nodes.items():
        if n["kind"] == "collect":
            assert "record_slots" in n["tools"], nid


def test_gmail_tools_scoped_to_gmail_node(spec):
    bad = copy.deepcopy(spec.raw)
    bad["nodes"]["need"]["tools"].append("push_gmail_connect")
    with pytest.raises(SpecError, match="gmail-only"):
        validate(FlowSpec(bad))


def test_unknown_tool_rejected(spec):
    bad = copy.deepcopy(spec.raw)
    bad["nodes"]["need"]["tools"].append("send_email")
    with pytest.raises(SpecError, match="unregistered tool"):
        validate(FlowSpec(bad))


def test_voice_ask_order_cannot_include_text_only_slot(spec):
    bad = copy.deepcopy(spec.raw)
    bad["ask_order"]["voice"].insert(0, "agent_name")
    with pytest.raises(SpecError, match="not collectable on voice"):
        validate(FlowSpec(bad))


def test_retry_budgets(spec):
    assert spec.retry_budget("user_name") == 3
    assert spec.retry_budget("gmail") == 2
