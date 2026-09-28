"""The `record_slots` tool: one strict schema covering every slot + intent in flow.yaml.

Generated from the spec so a new slot or intent can never be silently unextractable.
Output is deterministic (spec order) so the cached tools prefix stays byte-stable.
"""
from __future__ import annotations

from typing import Any, Optional

from ..brain.spec import FlowSpec

TOOL_NAME = "record_slots"


def record_slots_tool(spec: FlowSpec, *, compact: bool = False,
                      answers: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """`compact` (voice, LAT-001/LAT-003): slots the user did not give are omitted instead of
    sent as null, and there is no per-slot confidence (on a call every spoken name is read
    back and the STT's confidence is used), so the tool call is ~1/3 the output tokens (every
    token is on the caller's wait). Same slot names and intents; `extract.parse` treats
    missing as null. `answers` (voice, VQA-001): {id: what it answers} adds an optional
    `answer` enum: the approved answer to a question the caller asked (code speaks it)."""
    names = list(spec.slots)
    absent = ("Omit it if the user did not give it in THIS message." if compact
              else "null if the user did not give it in THIS message.")
    slot_props = {
        n: {
            "type": "string" if compact else ["string", "null"],
            "description": f"{spec.slots[n]['description']} {absent}",
        }
        for n in names
    }
    conf_props = {
        n: {"type": "number", "description": f"0.0-1.0 confidence in slots.{n}"
            + (f" (only when slots.{n} is given)." if compact else " (0 when null).")}
        for n in names
    }
    props: dict[str, Any] = {
        "slots": {
            "type": "object",
            "additionalProperties": False,
            "required": [] if compact else names,
            "properties": slot_props,
        },
    }
    if not compact:
        props["confidence"] = {
            "type": "object",
            "additionalProperties": False,
            "required": names,
            "properties": conf_props,
        }
    props["intents"] = {
        "type": "array",
        "items": {"type": "string", "enum": list(spec.intents)},
        "description": ("All intents that apply; empty if it is a plain answer. refuse_slot only "
                        "for an explicit no to the slot being asked; wanting more or other "
                        "help is a need, not a refusal."),
    }
    if answers:
        props["answer"] = {
            "type": "string",
            "enum": list(answers),
            "description": ("Only if the user asked a question: the id whose topic answers it. "
                            "Omit if they asked nothing or no id fits. Ids: "
                            + "; ".join(f"{k} = {v}" for k, v in answers.items()) + "."),
        }
    return {
        "name": TOOL_NAME,
        "description": (
            "Record what the user's latest message contains: a value for every slot they "
            "gave (in any order), " + ("" if compact else "per-slot confidence, ")
            + "and every intent that applies. "
            + ("Only include slots given in this message; omit the rest. " if compact else "")
            + "Call exactly once per user message."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["slots", "intents"] if compact else ["slots", "confidence", "intents"],
            "properties": props,
        },
    }
