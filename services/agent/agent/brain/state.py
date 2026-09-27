"""Session state — mirrors infra/supabase/migrations/0001_init.sql `sessions`.

Pure dataclasses; persistence lives in agent/store (FLOW-003).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

Channel = Literal["text", "voice"]
SlotStatus = Literal["empty", "candidate", "filled", "skipped"]


@dataclass
class SlotValue:
    value: Optional[str] = None
    status: SlotStatus = "empty"
    source: Optional[Channel] = None
    confidence: Optional[float] = None      # STT/extraction confidence, if any
    validated_by: Optional[str] = None      # validator id, e.g. "gmail_oauth"
    attempts: int = 0
    needs_confirm: bool = False             # candidate awaiting an explicit affirm (joke name, spell-back)


@dataclass
class SessionState:
    session_id: str
    version: int = 0                         # optimistic concurrency; bump on every write
    node: str = "greet"
    active_channel: Optional[Channel] = None
    slots: dict[str, SlotValue] = field(default_factory=dict)
    node_attempts: dict[str, int] = field(default_factory=dict)
    deferred_prompts: list[str] = field(default_factory=list)
    explained: list[str] = field(default_factory=list)   # nodes whose `why` was already given
    call_offer_resolved: bool = False        # call offered and answered (accepted/declined/implicit)
    graduated: bool = False
    call_lease_holder: Optional[str] = None  # call id holding the voice lease (double-dial lock)

    def slot(self, name: str) -> SlotValue:
        return self.slots.setdefault(name, SlotValue())

    def filled(self, slot: str) -> bool:
        return self.slots.get(slot, SlotValue()).status == "filled"

    def resolved(self, slot: str) -> bool:
        return self.slots.get(slot, SlotValue()).status in ("filled", "skipped")
