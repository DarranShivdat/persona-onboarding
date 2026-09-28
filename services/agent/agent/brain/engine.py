"""Flow engine: the pure transition function shared by text and voice.

Contract (pure, deterministic, no I/O):

    apply(spec, state, turn) -> TurnResult

`turn` carries the channel, the raw utterance, and an `Extraction` produced by
the LLM adapter (all-slot candidates + intents), or a UI/call `event`. The engine:
  1. runs validators on every candidate (only validated slots become `filled`;
     typed/spoken email becomes `candidate`, never `filled` — only a turn with
     `oauth_verified=True` from the OAuth callback fills gmail),
  2. applies intents (change_answer overwrites; refuse_slot escalates the retry
     ladder; insist_graduate graduates; prompt_injection changes nothing;
     noise_or_fragment is absorbed),
     Before any of that, the per-node acceptance table (flow.yaml `acceptance`, slot
     `channels` / `extraction`) drops what is not meaningful here — e.g. agent_name on a
     call, decline_call away from the offer — and logs each drop as `rejected_extraction`
     (GUARD-001). The LLM's extraction can never do more than this table allows.
  3. picks the next node = first unresolved slot in the channel's ask order
     (text: agent_name, then call_offer, first), honoring retry budgets
     (reask -> explain_why -> skip/defer),
  4. returns the new state, a `ResponsePlan` (what the phrasing layer must say)
     and a deterministic list of events to persist.

The input state is never mutated.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Optional

from .spec import START_NODE, TERMINAL_NODE, FlowSpec
from .state import Channel, SessionState, SlotValue
from .validators import VALIDATORS, Validation, assemble_spelling

# Turn-level events (no utterance): open = session start / return visit.
EVENTS = ("open", "call_started", "call_ended")
# Intents the phrasing layer answers briefly before steering back; never an attempt.
RESPOND_INTENTS = ("off_topic", "privacy_question", "other_language", "abuse")
GMAIL_CARD = "gmail_connect_card"


@dataclass
class Extraction:
    slots: dict[str, Optional[str]] = field(default_factory=dict)
    confidences: dict[str, float] = field(default_factory=dict)
    intents: list[str] = field(default_factory=list)


@dataclass
class Turn:
    channel: Channel
    utterance: str = ""
    extraction: Extraction = field(default_factory=Extraction)
    oauth_verified: bool = False            # set ONLY by the OAuth callback, never by the LLM
    event: Optional[str] = None             # one of EVENTS; extraction is ignored


@dataclass
class ResponsePlan:
    node: str = START_NODE                                 # node after this turn
    acknowledge: list[str] = field(default_factory=list)   # slots just filled
    changed: list[str] = field(default_factory=list)       # filled slots overwritten (change_answer)
    ask: Optional[str] = None                              # slot to ask for next
    confirm: Optional[str] = None                          # slot whose candidate needs a yes/no
    denied: list[str] = field(default_factory=list)        # candidates the user just said "no" to
    spell: Optional[str] = None                            # ask the user to spell this slot, letter by letter
    explain_why: bool = False
    suggest_examples: bool = False                         # need: offer 3 concrete examples
    rejected: dict[str, str] = field(default_factory=dict) # slot -> validator reason
    skipped: list[str] = field(default_factory=list)       # slots deferred this turn
    respond_to: list[str] = field(default_factory=list)    # intents to answer before steering back
    offer_call: bool = False
    absorbed: bool = False                                 # noise: say nothing / minimal backchannel
    resume: bool = False                                   # welcome back / "we got cut off"
    say: list[str] = field(default_factory=list)           # say-nodes to voice (greet, value_demo)
    deferred: list[str] = field(default_factory=list)
    push_ui: list[str] = field(default_factory=list)       # e.g. ["gmail_connect_card"]
    graduate: bool = False
    note: Optional[str] = None                             # home: a computed answer (e.g. "5 + 5 is 10")


@dataclass
class TurnResult:
    state: SessionState
    plan: ResponsePlan
    events: list[dict] = field(default_factory=list)


def apply(spec: FlowSpec, state: SessionState, turn: Turn) -> TurnResult:
    st = copy.deepcopy(state)
    plan = ResponsePlan(node=st.node)
    ev: list[dict] = []
    if st.graduated:
        # Returning after graduation lands in the main experience; onboarding never re-opens.
        if turn.event is None:
            # Home conversation (GRAD-001): edits through the same validators, honest answers.
            from .home import apply_home
            return apply_home(spec, state, turn)
        plan.graduate = True
        plan.deferred = list(st.deferred_prompts)
        if turn.event != "call_started":
            # The closing line is said once, when the session graduates. A call ending (or a
            # re-open) afterwards must not repeat it in the chat (live test 2026-09-28: the
            # full "You're all set, ..." summary reappeared after the call).
            plan.absorbed = True
        return TurnResult(st, plan, ev)
    if turn.event is not None:
        return _apply_event(spec, st, turn, plan, ev)

    intents = [i for i in turn.extraction.intents if i in spec.intents]
    intents = _accept_intents(spec, st.node, turn.channel, intents, ev)
    ev.extend({"type": "intent", "intent": i} for i in intents)
    if "prompt_injection" in intents:
        # Structural defense: nothing in an injection turn can move state.
        ev.append({"type": "injection_ignored", "node": state.node})
        plan.respond_to.append("prompt_injection")
        _describe(spec, state, plan)
        return TurnResult(copy.deepcopy(state), plan, ev)

    node = st.node
    touched, rejected, unsure = _extract(spec, st, turn, intents, plan, ev)
    confirm_handled = _resolve_confirms(spec, st, touched, intents, plan, ev)
    confirm_handled = _reopen_name(spec, st, turn, intents, touched, plan, ev) or confirm_handled

    if "noise_or_fragment" in intents and not touched and not confirm_handled:
        ev.append({"type": "absorbed", "node": node})
        plan.absorbed = True
        return TurnResult(st, plan, ev)

    plan.respond_to.extend(i for i in intents if i in RESPOND_INTENTS)
    unsure = (unsure or "unsure_need" in intents) and _slot_of(spec, node) == "need"
    plan.suggest_examples = unsure
    _call_intents(spec, st, turn, intents, touched, plan, ev)

    if "insist_graduate" in intents:
        return _graduate(spec, st, plan, ev, "insist_graduate")

    slot = _slot_of(spec, node)
    kind = spec.nodes[node]["kind"]
    if kind == "collect" and not st.resolved(slot) and slot not in touched and not confirm_handled:
        refused = "refuse_slot" in intents
        if refused or slot in rejected or unsure or not (touched or plan.respond_to):
            _fail(spec, st, node, refused, plan, ev)
    elif kind == "choice" and not st.call_offer_resolved and not plan.respond_to:
        _fail(spec, st, node, "refuse_slot" in intents, plan, ev)

    return _advance(spec, st, turn.channel, plan, ev)


# --- steps -------------------------------------------------------------------


def _rejected(ev, node, channel, reason, **what):
    ev.append({"type": "rejected_extraction", "node": node, "channel": channel, "reason": reason, **what})


def _accept_intents(spec, node, channel, intents, ev) -> list[str]:
    """GUARD-001: drop intents that are not meaningful on this node/channel (logged)."""
    kept = []
    for i in intents:
        if spec.accepts_intent(i, node, channel):
            kept.append(i)
        else:
            _rejected(ev, node, channel, "intent_not_accepted_here", intent=i)
    return kept


def _extract(spec, st, turn, intents, plan, ev):
    """Validate every extracted slot. Returns (touched, rejected, unsure)."""
    x = turn.extraction
    changing = "change_answer" in intents
    spelled = _spelled_name(spec, st, turn)
    touched: set[str] = set()
    rejected: set[str] = set()
    unsure = False
    for name, sdef in spec.slots.items():
        raw = spelled if name == NAME_SLOT and spelled else x.slots.get(name)
        if not raw:
            continue
        if not spec.accepts_slot(name, turn.channel):
            # e.g. agent_name is collected in text only: a value heard on a call is ignored.
            _rejected(ev, st.node, turn.channel, "slot_not_on_channel", slot=name)
            continue
        vid = sdef["validator"]
        kw = {"channel": turn.channel, "confidence": x.confidences.get(name)}
        if vid == "gmail_oauth":
            kw["oauth_verified"] = turn.oauth_verified
        if vid == "person_name":
            kw.update(utterance=turn.utterance, confirm_policy=sdef.get("confirm_policy"))
        res = VALIDATORS[vid](raw, **kw)
        if res.outcome == "ok" and spec.candidate_only(name) and not turn.oauth_verified:
            # Belt and braces over the validator: extraction never fills this slot.
            _rejected(ev, st.node, turn.channel, "extraction_cannot_fill", slot=name)
            res = Validation("candidate", res.value, reason="needs_oauth")
        sv = st.slot(name)
        if res.outcome == "unsure":
            unsure = True
            continue
        if res.outcome == "reject":
            rejected.add(name)
            plan.rejected[name] = res.reason
            ev.append({"type": "slot_rejected", "slot": name, "reason": res.reason})
            continue
        if sv.status == "filled":
            # Filled slots change only on an explicit change_answer (or a fresh OAuth), and
            # only to another fully valid value — never downgraded to a candidate. Exception:
            # a spoken name correction is read back before it replaces the name (NAME-001).
            reread = name == NAME_SLOT and res.outcome == "confirm" and changing
            if (res.outcome != "ok" and not reread) or not (changing or turn.oauth_verified) \
                    or res.value == sv.value:
                continue
            if reread:
                # The old spoken name already missed once: read the new one back ("So that's ...?").
                sv.confirm_attempts = 1
            else:
                ev.append({"type": "slot_changed", "slot": name, "old": sv.value, "new": res.value})
                plan.changed.append(name)
        elif res.outcome == "ok":
            ev.append({"type": "slot_filled", "slot": name, "value": res.value, "validated_by": vid})
            plan.acknowledge.append(name)
        touched.add(name)
        sv.value, sv.source, sv.confidence = res.value, turn.channel, x.confidences.get(name)
        if res.outcome == "ok":
            sv.status, sv.validated_by, sv.needs_confirm, sv.low_confidence = "filled", vid, False, False
        elif name == NAME_SLOT and res.outcome == "confirm" and \
                sv.confirm_attempts >= _name_policy(spec).get("max_attempts", NAME_MAX_ATTEMPTS):
            # Read-back cap: keep this (latest, best) candidate and move on; never trap the user.
            _fill_low_confidence(spec, st, name, plan, ev)
        else:
            sv.status, sv.validated_by, sv.needs_confirm = "candidate", None, res.outcome == "confirm"
            ev.append({"type": "slot_candidate", "slot": name, "value": res.value, "reason": res.reason})
            if sv.needs_confirm:
                plan.confirm = name
                sv.confirm_attempts += 1
    return touched, rejected, unsure


def _resolve_confirms(spec, st, touched, intents, plan, ev) -> bool:
    handled = False
    for name in spec.slots:
        sv = st.slots.get(name)
        if sv is None or not sv.needs_confirm or name in touched:
            continue
        implicit = _implicit_yes(name, touched, intents)
        if "affirm" in intents or implicit:
            sv.status, sv.needs_confirm = "filled", False
            sv.validated_by = spec.slots[name]["validator"]
            ev.append({"type": "slot_filled", "slot": name, "value": sv.value, "validated_by": sv.validated_by,
                       **({"implicit_confirm": True} if implicit else {})})
            # Implicit yes: the name's ack ("Perfect, thanks Sam.") comes before the new info's.
            plan.acknowledge.insert(0, name) if implicit else plan.acknowledge.append(name)
            handled = True
        elif "deny" in intents:
            handled = True
            if name == NAME_SLOT:
                _deny_name(spec, st, sv, plan, ev)
                continue
            st.slots[name] = SlotValue(attempts=sv.attempts)
            ev.append({"type": "slot_cleared", "slot": name})
        else:
            plan.confirm = name  # still waiting on the yes/no
    return handled


def _implicit_yes(name, touched, intents) -> bool:
    """NAME-002: the caller answered the name read-back with new information ("I mostly
    want help with my inbox") instead of yes/no: that is a yes. The new info was already
    extracted and validated this turn (`touched`). A correction (a new name, change_answer)
    or a "no" is never an implicit yes: those re-confirm / re-ask as before."""
    return (name == NAME_SLOT and bool(touched - {name})
            and not {"deny", "change_answer"} & set(intents))


def _call_intents(spec, st, turn, intents, touched, plan, ev):
    at_offer = spec.nodes[st.node]["kind"] == "choice"
    if "accept_call" in intents and st.active_channel != "voice":
        st.call_offer_resolved = True
        plan.push_ui.append("start_call")
        ev.append({"type": "call_requested", "node": st.node})
    elif "prefer_typing" in intents and st.active_channel == "voice":
        _set_channel(st, "text", ev)
        plan.push_ui.append("end_call")
    elif at_offer and ({"decline_call", "prefer_typing"} & set(intents) or touched):
        # Declining, or just answering the next questions by typing, keeps us in text.
        st.call_offer_resolved = True
        _set_channel(st, "text", ev)


def _fail(spec, st, node, refused, plan, ev):
    """One failed attempt at `node`: reask -> explain_why -> skip (deferred)."""
    budget = spec.retry_budget(node)
    slot = _slot_of(spec, node)
    n = st.node_attempts.get(node, 0) + 1
    explained = node in st.explained or bool(slot and spec.slots[slot].get("explain_on_first_ask"))
    if refused:
        # An explicit "no" gets the why at most once, then is respected.
        n = budget if explained else max(n, budget - 1)
    st.node_attempts[node] = n
    action = "skip" if n >= budget else "explain_why" if n == budget - 1 else "reask"
    ev.append({"type": "attempt", "node": node, "attempt": n, "action": action})
    if slot:
        st.slot(slot).attempts = n
    if action == "explain_why":
        plan.explain_why = True
        st.explained.append(node)
    elif action == "skip":
        if slot:
            st.slot(slot).status = "skipped"
            st.slot(slot).needs_confirm = False
            plan.skipped.append(slot)
            ev.append({"type": "slot_skipped", "slot": slot})
        else:
            st.call_offer_resolved = True
            _set_channel(st, "text", ev)


def _advance(spec, st, channel, plan, ev) -> TurnResult:
    target = _next_node(spec, st, channel)
    if target is None:
        return _graduate(spec, st, plan, ev, "complete")
    _move(spec, st, target, plan, ev)
    _describe(spec, st, plan)
    if plan.explain_why and st.node not in st.explained:
        st.explained.append(st.node)
    return TurnResult(st, plan, ev)


def _graduate(spec, st, plan, ev, reason) -> TurnResult:
    frm = st.node
    if st.filled("need"):
        plan.say.append("value_demo")
    if spec.nodes[frm].get("next_when_filled") == "value_demo" and st.filled("need"):
        ev.append({"type": "transition", "from": frm, "to": "value_demo"})
        frm = "value_demo"
    ev.append({"type": "transition", "from": frm, "to": TERMINAL_NODE})
    st.node, st.graduated = TERMINAL_NODE, True
    st.deferred_prompts = [s for s, d in spec.slots.items() if d["required"] and not st.filled(s)]
    ev.append({"type": "graduated", "reason": reason, "deferred": list(st.deferred_prompts)})
    plan.node, plan.graduate, plan.deferred = TERMINAL_NODE, True, list(st.deferred_prompts)
    plan.ask = plan.confirm = None
    return TurnResult(st, plan, ev)


def _apply_event(spec, st, turn, plan, ev) -> TurnResult:
    if turn.event not in EVENTS:
        raise ValueError(f"unknown turn event {turn.event!r}")
    ev.append({"type": "event", "event": turn.event, "channel": turn.channel})
    if turn.event == "call_started":
        st.call_offer_resolved = True
        _set_channel(st, "voice", ev)
    elif turn.event == "call_ended":
        # Hangup: keep every committed slot; the text side offers to call back or type.
        _set_channel(st, None, ev)
    if st.node == START_NODE:
        plan.say.append(START_NODE)
    else:
        plan.resume = True
    return _advance(spec, st, turn.channel, plan, ev)


# --- user_name read-back (NAME-001) --------------------------------------------

NAME_SLOT = "user_name"
NAME_SPELL_AFTER = 2
NAME_MAX_ATTEMPTS = 3
# "you've got my name wrong" / "that's not how you spell it": a correction with no new value.
_NAME_WRONG = re.compile(r"\b(my name|spell|spelt|spelled|misspel\w*)\b", re.IGNORECASE)


def _name_policy(spec) -> dict:
    return spec.slots.get(NAME_SLOT, {}).get("confirm_policy") or {}


def _spelled_name(spec, st, turn) -> Optional[str]:
    """Voice: letters spelled out in the transcript ("D as in David, double R...") beat the
    LLM's reading of them. Only where a name is in play (asked, pending, or extracted)."""
    if turn.channel != "voice" or NAME_SLOT not in spec.slots:
        return None
    sv = st.slots.get(NAME_SLOT)
    in_play = (_slot_of(spec, st.node) == NAME_SLOT or turn.extraction.slots.get(NAME_SLOT)
               or (sv is not None and sv.needs_confirm))
    letters = assemble_spelling(turn.utterance) if in_play else None
    return "-".join(letters) if letters else None


def _deny_name(spec, st, sv, plan, ev):
    """ "No" to a read-back: re-ask; after `spell_after` misses ask for letters; at the cap
    keep the best candidate (filled, low_confidence) and move on."""
    pol = _name_policy(spec)
    if sv.confirm_attempts >= pol.get("max_attempts", NAME_MAX_ATTEMPTS):
        _fill_low_confidence(spec, st, NAME_SLOT, plan, ev)
        return
    st.slots[NAME_SLOT] = SlotValue(attempts=sv.attempts, confirm_attempts=sv.confirm_attempts)
    ev.append({"type": "slot_cleared", "slot": NAME_SLOT})
    plan.denied.append(NAME_SLOT)
    if st.active_channel == "voice" or sv.source == "voice":
        if sv.confirm_attempts >= pol.get("spell_after", NAME_SPELL_AFTER):
            plan.spell = NAME_SLOT


def _fill_low_confidence(spec, st, name, plan, ev):
    sv = st.slot(name)
    vid = spec.slots[name]["validator"]
    sv.status, sv.validated_by, sv.needs_confirm, sv.low_confidence = "filled", vid, False, True
    ev.append({"type": "slot_filled", "slot": name, "value": sv.value, "validated_by": vid,
               "low_confidence": True})
    plan.acknowledge.append(name)
    plan.confirm = None


def _reopen_name(spec, st, turn, intents, touched, plan, ev) -> bool:
    """ "You've got my name wrong" (change_answer, no new value): reopen user_name and ask
    again — to spell it on a call, since the spoken name already failed once."""
    if ("change_answer" not in intents or touched or NAME_SLOT not in spec.slots
            or not st.filled(NAME_SLOT) or not _NAME_WRONG.search(turn.utterance or "")):
        return False
    old = st.slot(NAME_SLOT)
    st.slots[NAME_SLOT] = SlotValue(attempts=old.attempts, confirm_attempts=1)
    ev.append({"type": "slot_cleared", "slot": NAME_SLOT, "reason": "user_correction"})
    plan.denied.append(NAME_SLOT)
    if turn.channel == "voice":
        plan.spell = NAME_SLOT
    return True


# --- helpers -----------------------------------------------------------------


def _slot_of(spec, node) -> Optional[str]:
    return spec.nodes[node].get("slot")


def _collect_node(spec, slot) -> str:
    return next(nid for nid, n in spec.nodes.items() if n["kind"] == "collect" and n["slot"] == slot)


def _next_node(spec, st, channel) -> Optional[str]:
    ch = st.active_channel or channel
    order = spec.ask_order(ch)
    if ch == "text":
        voice = set(spec.ask_order("voice"))
        pre_call = [s for s in order if s not in voice]  # text-only slots precede the call offer
        for s in pre_call:
            if not st.resolved(s):
                return _collect_node(spec, s)
        offer = next(nid for nid, n in spec.nodes.items() if n["kind"] == "choice")
        # The offer only follows the pre-call nodes; once past it, the call button stays
        # available in the UI but the flow never walks back to the offer.
        before_offer = {START_NODE, offer} | {_collect_node(spec, s) for s in pre_call}
        if not st.call_offer_resolved and st.node in before_offer:
            return offer
    for s in order:
        if not st.resolved(s):
            return _collect_node(spec, s)
    return None


def _move(spec, st, to, plan, ev):
    frm = st.node
    if frm == to:
        return
    if spec.nodes[frm]["kind"] == "say" and spec.nodes[frm]["next"] != to:
        ev.append({"type": "transition", "from": frm, "to": spec.nodes[frm]["next"]})
        frm = spec.nodes[frm]["next"]
    ev.append({"type": "transition", "from": frm, "to": to})
    st.node = to


def _describe(spec, st, plan):
    """What to ask at the current node (state is read, never written)."""
    node = st.node
    n = spec.nodes[node]
    plan.node = node
    plan.offer_call = n["kind"] == "choice"
    if n["kind"] != "collect":
        return
    slot = n["slot"]
    if plan.confirm is None:
        plan.ask = slot
    if (st.node_attempts.get(node, 0) == 0 and spec.slots[slot].get("explain_on_first_ask")
            and node not in st.explained):
        # Explain once per session; re-entering the node (e.g. after a correction)
        # must not repeat the pitch.
        plan.explain_why = True
    if "push_gmail_connect" in n["tools"] and GMAIL_CARD not in plan.push_ui:
        plan.push_ui.append(GMAIL_CARD)


def _set_channel(st, ch, ev):
    if st.active_channel != ch:
        ev.append({"type": "channel", "from": st.active_channel, "to": ch})
        st.active_channel = ch
