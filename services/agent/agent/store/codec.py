"""SessionState <-> `sessions` row mapping (pure; no DB access)."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..brain.spec import TERMINAL_NODE
from ..brain.state import SessionState, SlotValue

# Columns the brain owns; a turn UPDATE writes exactly these (never the call lease).
BRAIN_COLUMNS = (
    "node", "active_channel", "slots", "node_attempts", "deferred_prompts",
    "explained", "call_offer_resolved", "status",
)


def state_to_columns(state: SessionState) -> dict[str, Any]:
    return {
        "node": state.node,
        "active_channel": state.active_channel,
        "slots": {name: asdict(sv) for name, sv in state.slots.items()},
        "node_attempts": dict(state.node_attempts),
        "deferred_prompts": list(state.deferred_prompts),
        "explained": list(state.explained),
        "call_offer_resolved": state.call_offer_resolved,
        "status": "graduated" if state.graduated else "active",
    }


def state_from_row(row: dict[str, Any]) -> SessionState:
    fields = set(SlotValue.__dataclass_fields__)
    slots = {
        name: SlotValue(**{k: v for k, v in (raw or {}).items() if k in fields})
        for name, raw in (row.get("slots") or {}).items()
    }
    return SessionState(
        session_id=str(row["id"]),
        version=row["version"],
        node=row["node"],
        active_channel=row.get("active_channel"),
        slots=slots,
        node_attempts=dict(row.get("node_attempts") or {}),
        deferred_prompts=list(row.get("deferred_prompts") or []),
        explained=list(row.get("explained") or []),
        call_offer_resolved=bool(row.get("call_offer_resolved")),
        graduated=row.get("status") == "graduated" or row["node"] == TERMINAL_NODE,
        call_lease_holder=row.get("call_lease_holder"),
    )
