"""The `record_slots` tool: one strict schema covering every slot + intent in flow.yaml.

Generated from the spec so a new slot or intent can never be silently unextractable.
Output is deterministic (spec order) so the cached tools prefix stays byte-stable.
"""
from __future__ import annotations

from typing import Any

from ..brain.spec import FlowSpec

TOOL_NAME = "record_slots"


def record_slots_tool(spec: FlowSpec) -> dict[str, Any]:
    names = list(spec.slots)
    slot_props = {
        n: {
            "type": ["string", "null"],
            "description": f"{spec.slots[n]['description']} null if the user did not give it in THIS message.",
        }
        for n in names
    }
    conf_props = {
        n: {"type": "number", "description": f"0.0-1.0 confidence in slots.{n} (0 when null)."}
        for n in names
    }
    return {
        "name": TOOL_NAME,
        "description": (
            "Record what the user's latest message contains: a value for every slot they "
            "gave (in any order), per-slot confidence, and every intent that applies. "
            "Call exactly once per user message."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["slots", "confidence", "intents"],
            "properties": {
                "slots": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": names,
                    "properties": slot_props,
                },
                "confidence": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": names,
                    "properties": conf_props,
                },
                "intents": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(spec.intents)},
                    "description": "All intents that apply; empty if it is a plain answer.",
                },
            },
        },
    }
