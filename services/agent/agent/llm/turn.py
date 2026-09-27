"""Thin turn helper: extract -> brain.apply -> phrase. No I/O, no persistence.

FLOW-003 wraps this with the Postgres transaction (version check), SSE pushes and the
HTTP API; the voice adapter calls the same function. State moves ONLY via
`brain.apply` on validated extractions (invariant 1).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..brain.engine import Extraction, ResponsePlan, Turn, TurnResult, apply
from ..brain.spec import FlowSpec
from ..brain.state import Channel, SessionState
from ..obs.meta import turn_metadata
from ..obs.tracing import NoopTracer, Tracer
from . import templates as T
from .extract import ExtractionResult, Extractor, turn_context
from .phrase import PhraseResult, Phraser, template_reply


@dataclass
class TurnOutput:
    result: TurnResult                  # new state + plan + events (state unchanged on failure)
    reply: str
    extraction: Optional[ExtractionResult] = None
    phrase: Optional[PhraseResult] = None
    extraction_failed: bool = False


def run_turn(spec: FlowSpec, state: SessionState, *, channel: Channel, utterance: str = "",
             extractor: Extractor, phraser: Phraser, event: Optional[str] = None,
             oauth_verified: bool = False, oauth_email: Optional[str] = None,
             last_assistant: Optional[str] = None, tracer: Optional[Tracer] = None) -> TurnOutput:
    tracer = tracer or NoopTracer()
    tid = tracer.start_trace(session_id=state.session_id, channel=channel, name="turn",
                             metadata=turn_metadata(spec, state.node, event=event))
    xres: Optional[ExtractionResult] = None
    if event is not None:
        turn = Turn(channel=channel, event=event)
    elif oauth_verified:
        # The OAuth callback is code, not LLM: it is the only path that fills gmail.
        turn = Turn(channel=channel, oauth_verified=True,
                    extraction=Extraction(slots={"gmail": oauth_email or ""}))
    else:
        ctx = turn_context(spec, state, channel, last_assistant)
        xres = extractor.extract(utterance, ctx, trace_id=tid)
        if not xres.ok:
            # No usable extraction: state untouched, templated re-ask of the current node.
            out = _reask(spec, state, channel, xres)
            tracer.span(tid, name="extract_failed", input=utterance, output=out.reply,
                        metadata={"error": xres.error})
            return out
        turn = Turn(channel=channel, utterance=utterance, extraction=xres.extraction)

    result = apply(spec, state, turn)
    tracer.span(tid, name="brain.apply", input={"node": state.node},
                output={"node": result.plan.node, "events": result.events})
    pres = phraser.phrase(result.plan, result.state, channel, utterance=utterance, trace_id=tid)
    return TurnOutput(result, pres.text, xres, pres)


def _reask(spec: FlowSpec, state: SessionState, channel: Channel, xres: ExtractionResult) -> TurnOutput:
    node = spec.nodes[state.node]
    plan = ResponsePlan(node=state.node, ask=node.get("slot"), offer_call=node.get("kind") == "choice")
    rest = template_reply(spec, plan, state, channel, critical=False)
    reply = " ".join(p for p in (T.REASK, rest) if p)
    return TurnOutput(TurnResult(state, plan, []), reply, xres, None, extraction_failed=True)
