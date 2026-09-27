"""Flow engine interface (implementation: roadmap packet FLOW-001).

Contract (pure, deterministic, no I/O):

    apply(spec, state, turn) -> TurnResult

`turn` carries the channel, the raw utterance, and an `Extraction` produced by
the LLM adapter (all-slot candidates + intents). The engine:
  1. runs validators on every candidate (only validated slots become `filled`;
     spoken email becomes `candidate`, never `filled`),
  2. applies intents (change_answer overwrites; refuse_slot counts an attempt;
     insist_graduate / need-filled enables graduation),
  3. picks the next node = first unfilled slot in the channel's ask order,
     honoring retry budgets (reask -> explain_why -> skip/defer),
  4. returns the next node, a `ResponsePlan` (what the phrasing layer must say:
     acknowledgements, the ask, the why, deferred items), and events to persist.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .spec import FlowSpec
from .state import Channel, SessionState


@dataclass
class Extraction:
    slots: dict[str, Optional[str]] = field(default_factory=dict)
    confidences: dict[str, float] = field(default_factory=dict)
    intents: list[str] = field(default_factory=list)


@dataclass
class Turn:
    channel: Channel
    utterance: str
    extraction: Extraction


@dataclass
class ResponsePlan:
    acknowledge: list[str] = field(default_factory=list)   # slots just filled
    ask: Optional[str] = None                              # slot to ask for next
    explain_why: bool = False
    deferred: list[str] = field(default_factory=list)
    push_ui: list[str] = field(default_factory=list)       # e.g. ["gmail_connect_card"]
    graduate: bool = False


@dataclass
class TurnResult:
    state: SessionState
    plan: ResponsePlan
    events: list[dict] = field(default_factory=list)


def apply(spec: FlowSpec, state: SessionState, turn: Turn) -> TurnResult:  # pragma: no cover
    raise NotImplementedError("FLOW-001: implement the pure transition function")
