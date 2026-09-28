"""Load and structurally validate packages/flow/flow.yaml.

This is real (not a stub): qa:flow runs its invariants on every change.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SPEC_PATH = REPO_ROOT / "packages" / "flow" / "flow.yaml"

# Tool registry: every tool a node may expose. Adding a tool = add here + implement
# in agent/tools (FLOW-002). Node tool scoping is validated against this set.
TOOL_REGISTRY = {
    "record_slots",          # the all-slot extraction function exposed on every collect node
    "start_call",            # hands the session to the voice channel (call_offer only)
    "push_gmail_connect",    # pushes the on-screen Connect Gmail card (gmail only)
    "capture_spoken_email",  # chunked spoken-email fallback (gmail only)
    "request_typed_email",   # "type it" escape during a call (gmail only)
}
GMAIL_ONLY_TOOLS = {"push_gmail_connect", "capture_spoken_email", "request_typed_email"}
TERMINAL_NODE = "graduated"
START_NODE = "greet"


class SpecError(ValueError):
    pass


@dataclass(frozen=True)
class FlowSpec:
    raw: dict[str, Any]

    @property
    def slots(self) -> dict[str, Any]:
        return self.raw["slots"]

    @property
    def nodes(self) -> dict[str, Any]:
        return self.raw["nodes"]

    @property
    def intents(self) -> list[str]:
        return self.raw["intents"]

    def ask_order(self, channel: str) -> list[str]:
        return self.raw["ask_order"][channel]

    def retry_budget(self, node: str) -> int:
        rb = self.raw["retry_budget"]
        return int(rb.get(node, rb["default"]))

    # --- GUARD-001: code-side acceptance of record_slots extractions ---------------

    def accepts_slot(self, slot: str, channel: str) -> bool:
        """An extracted value for `slot` is considered only on the slot's channels."""
        return channel in self.slots[slot]["channels"]

    def candidate_only(self, slot: str) -> bool:
        """Extraction can at most make this slot a candidate (gmail: OAuth fills it)."""
        return self.slots[slot].get("extraction") == "candidate_only"

    def accepts_intent(self, intent: str, node: str, channel: str) -> bool:
        """Intents with an acceptance rule count only on its nodes or channels; others anywhere."""
        rule = ((self.raw.get("acceptance") or {}).get("intents") or {}).get(intent)
        if rule is None:
            return True
        return node in (rule.get("nodes") or []) or channel in (rule.get("channels") or [])

    def successors(self, node_id: str) -> set[str]:
        n = self.nodes[node_id]
        out: set[str] = set()
        for key in ("next", "next_when_filled"):
            if n.get(key):
                out.add(n[key])
        out.update((n.get("on_intent") or {}).values())
        if n["kind"] != "terminal":
            out.add(TERMINAL_NODE)  # graduation is legal from any non-terminal node
            # out-of-order extraction may skip forward to any later collect node
            out.update(k for k, v in self.nodes.items() if v["kind"] == "collect")
        return out


def load_spec(path: Path | str = DEFAULT_SPEC_PATH) -> FlowSpec:
    data = yaml.safe_load(Path(path).read_text())
    spec = FlowSpec(data)
    validate(spec)
    return spec


def validate(spec: FlowSpec) -> None:
    raw = spec.raw
    for key in ("version", "id", "slots", "ask_order", "retry_budget", "graduation", "intents", "nodes"):
        if key not in raw:
            raise SpecError(f"missing top-level key: {key}")
    nodes, slots = spec.nodes, spec.slots
    if START_NODE not in nodes:
        raise SpecError(f"missing start node {START_NODE!r}")
    terminals = [k for k, v in nodes.items() if v["kind"] == "terminal"]
    if terminals != [TERMINAL_NODE]:
        raise SpecError(f"exactly one terminal node {TERMINAL_NODE!r} required, got {terminals}")
    for nid, n in nodes.items():
        for key in ("next", "next_when_filled"):
            if n.get(key) and n[key] not in nodes:
                raise SpecError(f"{nid}.{key} -> unknown node {n[key]!r}")
        for intent, target in (n.get("on_intent") or {}).items():
            if intent not in spec.intents:
                raise SpecError(f"{nid}.on_intent uses unknown intent {intent!r}")
            if target not in nodes:
                raise SpecError(f"{nid}.on_intent[{intent}] -> unknown node {target!r}")
        for tool in n["tools"]:
            if tool not in TOOL_REGISTRY:
                raise SpecError(f"{nid} exposes unregistered tool {tool!r}")
            if tool in GMAIL_ONLY_TOOLS and nid != "gmail":
                raise SpecError(f"{nid} exposes gmail-only tool {tool!r}")
        if n["kind"] == "collect":
            if n.get("slot") not in slots:
                raise SpecError(f"collect node {nid} names unknown slot {n.get('slot')!r}")
            if "record_slots" not in n["tools"]:
                raise SpecError(f"collect node {nid} must expose record_slots (all-slot extraction)")
    collected = {n.get("slot") for n in nodes.values() if n["kind"] == "collect"}
    for s in slots:
        if s not in collected:
            raise SpecError(f"slot {s!r} has no collect node")
    for ch in ("text", "voice"):
        for s in spec.ask_order(ch):
            if s not in slots:
                raise SpecError(f"ask_order.{ch} names unknown slot {s!r}")
            if ch not in slots[s]["channels"]:
                raise SpecError(f"slot {s!r} is not collectable on {ch} but is in ask_order.{ch}")
    for intent, rule in ((raw.get("acceptance") or {}).get("intents") or {}).items():
        if intent not in spec.intents:
            raise SpecError(f"acceptance names unknown intent {intent!r}")
        for nid in rule.get("nodes") or []:
            if nid not in nodes:
                raise SpecError(f"acceptance.intents.{intent} names unknown node {nid!r}")
        for ch in rule.get("channels") or []:
            if ch not in ("text", "voice"):
                raise SpecError(f"acceptance.intents.{intent} names unknown channel {ch!r}")
    for s, d in slots.items():
        if d.get("extraction", "fill") not in ("fill", "candidate_only"):
            raise SpecError(f"slot {s!r} has unknown extraction mode {d['extraction']!r}")
    # reachability
    seen, stack = set(), [START_NODE]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(spec.successors(cur) - seen)
    unreachable = set(nodes) - seen
    if unreachable:
        raise SpecError(f"unreachable nodes: {sorted(unreachable)}")
