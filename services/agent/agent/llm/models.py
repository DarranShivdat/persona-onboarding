"""Model defaults and per-model request policy for the LLM adapter.

Defaults (ARCHITECTURE §12: Haiku-class on the latency path, Sonnet as the escalation
if slot_correctness suffers). Re-score with `python -m agent.llm.eval` (offline replay
of tests/fixtures/llm/recordings/<model>/; record with PERSONA_QA_LIVE=1 ... --record):

  extraction (voice + text): claude-haiku-4-5   -- latency tier, forced tool_choice OK
  phrasing   (voice + text): claude-haiku-4-5   -- 1-2 short sentences, guarded

Override per deployment with env var names only:
  PERSONA_EXTRACT_MODEL, PERSONA_PHRASE_MODEL
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

DEFAULT_EXTRACT_MODEL = "claude-haiku-4-5"
DEFAULT_PHRASE_MODEL = "claude-haiku-4-5"
# Escalation target if slot_correctness regresses on the eval (ARCHITECTURE §12).
ESCALATION_MODEL = "claude-sonnet-5"


@dataclass(frozen=True)
class ModelPolicy:
    forced_tool_choice: bool           # False -> tool_choice auto + prompt instruction
    thinking: Optional[dict] = None    # sent verbatim when not None


# Forced tool_choice is rejected by Fable 5.1 / Mythos 5.1 / Opus 5.5, and is
# incompatible with thinking on models that think by default (Opus 5, Sonnet 5), so
# those either run auto or explicitly disable thinking. Unknown models default to
# auto: it works everywhere, and the adapter retries once if no tool call comes back.
_POLICIES: dict[str, ModelPolicy] = {
    "claude-haiku-4-5": ModelPolicy(forced_tool_choice=True),
    "claude-sonnet-4-6": ModelPolicy(forced_tool_choice=True),
    "claude-sonnet-5": ModelPolicy(forced_tool_choice=True, thinking={"type": "disabled"}),
    "claude-opus-4-8": ModelPolicy(forced_tool_choice=True),
    "claude-opus-5": ModelPolicy(forced_tool_choice=False),
    "claude-opus-5-5": ModelPolicy(forced_tool_choice=False),
    "claude-fable-5-1": ModelPolicy(forced_tool_choice=False),
    "claude-mythos-5-1": ModelPolicy(forced_tool_choice=False),
}
_AUTO = ModelPolicy(forced_tool_choice=False)


def policy_for(model: str) -> ModelPolicy:
    return _POLICIES.get(model, _AUTO)


def extract_model() -> str:
    return os.environ.get("PERSONA_EXTRACT_MODEL", DEFAULT_EXTRACT_MODEL)


def phrase_model() -> str:
    return os.environ.get("PERSONA_PHRASE_MODEL", DEFAULT_PHRASE_MODEL)
