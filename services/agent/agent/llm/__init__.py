"""LLM adapter (direct Anthropic SDK): record_slots extraction, constrained phrasing,
output guard, and the extract -> brain.apply -> phrase turn helper.

Extraction never writes state; only `brain.apply` does, on validated candidates.
"""
from .extract import ExtractionResult, Extractor, parse, turn_context  # noqa: F401
from .guard import GuardResult, guard  # noqa: F401
from .models import DEFAULT_EXTRACT_MODEL, DEFAULT_PHRASE_MODEL, policy_for  # noqa: F401
from .phrase import PhraseResult, Phraser  # noqa: F401
from .schema import TOOL_NAME, record_slots_tool  # noqa: F401
from .turn import TurnOutput, run_turn  # noqa: F401
