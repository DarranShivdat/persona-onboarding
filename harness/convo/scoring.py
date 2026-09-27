"""Deterministic convo-tier scores (no LLM judge; the live tier adds that).

slot_correctness = matched expected.state keys / all expected.state keys
on_track         = turns that neither re-ask an already-filled slot nor move the node
                   backwards (except on an intentional change_answer) / all turns
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.brain.spec import FlowSpec
from agent.brain.state import SessionState

SLOT_STATUSES = ("empty", "candidate", "filled", "skipped")
_WITH_VALUE = re.compile(r"^(empty|candidate|filled|skipped)\((.+)\)$")
THEN_CONFIRM = "candidate_then_filled_after_confirm"


@dataclass
class Check:
    key: str
    ok: bool
    want: Any
    got: Any


def check_state(spec: FlowSpec, expected: dict, st: SessionState, initial: SessionState,
                *, phase: str = "final") -> list[Check]:
    """Compare a final state against a catalog `expected.state` mapping.

    `phase="script"` evaluates `candidate_then_filled_after_confirm` as its first half
    (a candidate awaiting confirm); `phase="final"` as its second half (filled).
    """
    out: list[Check] = []
    for key, want in expected.items():
        if key in spec.slots:
            sv = st.slots.get(key)
            status = sv.status if sv else "empty"
            value = sv.value if sv else None
            if want == THEN_CONFIRM:
                if phase == "script":
                    out.append(Check(key, status == "candidate" and bool(sv and sv.needs_confirm),
                                     "candidate(needs_confirm)", status))
                else:
                    out.append(Check(key, status == "filled", "filled", status))
                continue
            m = _WITH_VALUE.match(str(want))
            if m:
                out.append(Check(key, (status, value) == (m.group(1), m.group(2)), want, f"{status}({value})"))
            elif want in SLOT_STATUSES:
                out.append(Check(key, status == want, want, status))
            else:
                raise ValueError(f"unknown slot expectation {key}: {want!r}")
        elif key == "node":
            target = initial.node if want == "unchanged" else want
            out.append(Check(key, st.node == target, target, st.node))
        elif key == "slots_unchanged":
            same = _slots(st) == _slots(initial)
            out.append(Check(key, same == bool(want), bool(want), same))
        elif key == "deferred_prompts":
            out.append(Check(key, sorted(st.deferred_prompts) == sorted(want), sorted(want), sorted(st.deferred_prompts)))
        elif key in ("graduated", "active_channel", "call_offer_resolved", "call_lease_holder"):
            out.append(Check(key, getattr(st, key) == want, want, getattr(st, key)))
        else:
            raise ValueError(f"unknown expected.state key {key!r}")
    return out


def slot_correctness(checks: list[Check]) -> float:
    return round(sum(c.ok for c in checks) / len(checks), 4) if checks else 1.0


@dataclass
class TurnTrace:
    node_before: str
    node_after: str
    filled_before: list[str]
    ask: Optional[str]
    intents: list[str] = field(default_factory=list)


def node_rank(spec: FlowSpec) -> dict[str, int]:
    # flow.yaml lists nodes in forward order: greet .. value_demo, graduated.
    return {n: i for i, n in enumerate(spec.nodes)}


def on_track(spec: FlowSpec, turns: list[TurnTrace]) -> tuple[float, list[str]]:
    if not turns:
        return 1.0, []
    rank = node_rank(spec)
    notes: list[str] = []
    good = 0
    for i, t in enumerate(turns):
        bad = []
        if t.ask and t.ask in t.filled_before and "change_answer" not in t.intents:
            bad.append(f"reasked filled slot {t.ask}")
        if rank[t.node_after] < rank[t.node_before] and "change_answer" not in t.intents:
            bad.append(f"node went back {t.node_before}->{t.node_after}")
        if bad:
            notes.append(f"turn {i}: " + "; ".join(bad))
        else:
            good += 1
    return round(good / len(turns), 4), notes


def check_plan(want: dict, plan: Any) -> list[Check]:
    out = []
    for key, v in want.items():
        got = getattr(plan, key)
        if isinstance(v, list) and isinstance(got, list):
            ok = all(x in got for x in v)
        else:
            ok = got == v
        out.append(Check(f"plan.{key}", ok, v, got))
    return out


def _slots(st: SessionState) -> dict:
    return {k: (v.status, v.value) for k, v in st.slots.items() if v.status != "empty" or v.value}
