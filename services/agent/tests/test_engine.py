"""Engine behavior tests — PENDING until FLOW-001 lands.

Each test names the edge-case id(s) from harness/edge-cases.yaml it will cover.
FLOW-001 replaces the skip with real assertions (pure, deterministic, no LLM).
"""
import pytest

pending = pytest.mark.skip(reason="PENDING: FLOW-001 (engine.apply not implemented)")

CASES = [
    ("out_of_order_answer_fills_and_skips_node", ["EC-17"]),
    ("change_answer_overwrites_filled_slot", ["EC-16"]),
    ("skip_everything_graduates_with_deferred_prompts", ["EC-23"]),
    ("retry_budget_reask_explain_then_skip", ["EC-14"]),
    ("typed_or_spoken_email_is_candidate_not_filled", ["EC-17", "EC-28"]),
    ("noise_fragment_absorbed_without_reask", ["EC-11"]),
    ("hangup_resume_picks_first_unfilled_slot", ["EC-01"]),
    ("injection_cannot_fill_slots", ["EC-26"]),
    ("decline_call_continues_in_text", ["EC-29"]),
    ("return_visit_resumes_or_lands_in_main_experience", ["EC-30", "EC-31"]),
]


@pending
@pytest.mark.parametrize("name,case_ids", CASES, ids=[c[0] for c in CASES])
def test_engine_behavior(name, case_ids):
    raise AssertionError("not implemented")
