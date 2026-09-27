"""Run convo-tier edge cases through the real text turn loop (`agent.llm.run_turn`).

Modes (all offline except `record --source live`):
  mock   : MockLLM answers extraction from harness/convo/cases.yaml fixtures
  replay : JsonlReplayClient replays fixtures/recordings/<case>.jsonl (real adapter
           parse path on recorded Anthropic-shaped responses)
  record : writes those recordings, from MockLLM (--source mock, provenance=synthetic)
           or the real API (--source live; needs PERSONA_QA_LIVE=1, costs money)
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

import yaml

from . import ROOT
from .scoring import THEN_CONFIRM, Check, TurnTrace, check_plan, check_state, on_track, slot_correctness

from agent.brain.spec import FlowSpec, load_spec  # noqa: E402
from agent.brain.state import SessionState, SlotValue  # noqa: E402
from agent.llm import Extractor, Phraser, run_turn  # noqa: E402
from agent.llm.models import DEFAULT_EXTRACT_MODEL, DEFAULT_PHRASE_MODEL, extract_model, phrase_model  # noqa: E402
from agent.llm.prompts import extraction_system, phrasing_system  # noqa: E402
from agent.llm.schema import record_slots_tool  # noqa: E402
from agent.llm.testing import JsonlReplayClient, MockLLM, RecordingClient  # noqa: E402
from harness.evals.interface import EvalBackend, ItemResult, LocalEvalBackend  # noqa: E402

CATALOG = ROOT / "harness" / "edge-cases.yaml"
BINDINGS = ROOT / "harness" / "convo" / "cases.yaml"
RECORDINGS = ROOT / "harness" / "convo" / "fixtures" / "recordings"
EVALS_ROOT = ROOT / ".persona-qa" / "evals"
TIER = "convo"
CHANNEL = "text"
LIVE_FLAG = "PERSONA_QA_LIVE"
MODES = ("mock", "replay")


# --- loading -----------------------------------------------------------------


def load_catalog(path: Path = CATALOG) -> dict:
    return yaml.safe_load(path.read_text())


def load_bindings(path: Path = BINDINGS) -> dict[str, dict]:
    return yaml.safe_load(path.read_text())["cases"]


def convo_cases(catalog: Optional[dict] = None, bindings: Optional[dict] = None) -> list[tuple[dict, dict]]:
    catalog = catalog or load_catalog()
    bindings = bindings if bindings is not None else load_bindings()
    out = []
    for c in catalog["cases"]:
        if TIER in c["tiers"]:
            out.append((c, bindings.get(c["id"]) or {"pending": "no binding in harness/convo/cases.yaml"}))
    return out


def initial_state(spec: FlowSpec, case_id: str, setup: dict) -> SessionState:
    node = setup.get("node", "greet")
    past_offer = spec.nodes[node]["kind"] == "collect" and node != "agent_name"
    st = SessionState(
        session_id=f"convo-{case_id}",
        node=node,
        active_channel=setup.get("active_channel", CHANNEL if past_offer else None),
        call_offer_resolved=setup.get("call_offer_resolved", past_offer),
    )
    for name, v in (setup.get("slots") or {}).items():
        v = v if isinstance(v, dict) else {"value": v}
        status = v.get("status", "filled")
        st.slots[name] = SlotValue(value=v.get("value"), status=status, source=CHANNEL,
                                   validated_by=spec.slots[name]["validator"] if status == "filled" else None)
    return st


def script_steps(case: dict, binding: dict) -> list[dict]:
    overrides = {int(k): v for k, v in (binding.get("utterances") or {}).items()}
    steps = []
    for i, s in enumerate(case["script"]):
        if "type" in s or "say" in s:
            steps.append({"utterance": overrides.get(i, s.get("type", s.get("say")))})
        else:
            steps.append({"action": s["action"]})
    return steps


# --- run identity --------------------------------------------------------------


def prompts_hash(spec: FlowSpec) -> str:
    blob = json.dumps([extraction_system(spec), phrasing_system(spec), record_slots_tool(spec)], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:10]


def git_sha() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
        return sha + ("+dirty" if dirty.strip() else "")
    except (OSError, subprocess.CalledProcessError):
        return "nogit"


def run_name(spec: FlowSpec) -> str:
    return f"{git_sha()}|flow-v{spec.raw['version']}|prompts-{prompts_hash(spec)}"


# --- network guard -------------------------------------------------------------


class NetworkGuard:
    """Blocks outbound sockets for offline modes and counts attempts."""

    def __init__(self) -> None:
        self.attempts: list[str] = []

    @contextlib.contextmanager
    def active(self) -> Iterator["NetworkGuard"]:
        saved = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)

        def deny(what):
            def _f(*a, **k):
                self.attempts.append(f"{what}{a[1:2] if what.startswith('socket.') else a[:1]}")
                raise OSError(f"network disabled in qa:{TIER} ({what})")
            return _f

        socket.socket.connect = deny("socket.connect")  # type: ignore[method-assign]
        socket.socket.connect_ex = deny("socket.connect_ex")  # type: ignore[method-assign]
        socket.create_connection = deny("create_connection")  # type: ignore[assignment]
        socket.getaddrinfo = deny("getaddrinfo")  # type: ignore[assignment]
        try:
            yield self
        finally:
            (socket.socket.connect, socket.socket.connect_ex,
             socket.create_connection, socket.getaddrinfo) = saved  # type: ignore[method-assign]


# --- running ---------------------------------------------------------------------


@dataclass
class TurnLog:
    step: str
    reply: str
    node: str
    intents: list[str]
    extraction_failed: bool = False


@dataclass
class CaseResult:
    case_id: str
    status: str                                   # pass | fail | pending
    scores: dict[str, float] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    turns: list[TurnLog] = field(default_factory=list)

    def summary(self) -> str:
        return "; ".join(self.failures or self.notes)


def recording_path(case_id: str, root: Path = RECORDINGS) -> Path:
    return root / f"{case_id}.jsonl"


def _mock(spec: FlowSpec, binding: dict) -> MockLLM:
    return MockLLM(spec, binding.get("extractions") or {}, faults=binding.get("faults") or ())


def run_case(spec: FlowSpec, case: dict, binding: dict, mode: str, *, client: Any = None,
             recordings: Path = RECORDINGS) -> CaseResult:
    cid = case["id"]
    if binding.get("pending"):
        return CaseResult(cid, "pending", notes=[f"PENDING: {' '.join(str(binding['pending']).split())}"])
    res = CaseResult(cid, "pass")
    models = (DEFAULT_EXTRACT_MODEL, DEFAULT_PHRASE_MODEL)
    if client is None:
        if mode == "mock":
            client = _mock(spec, binding)
        elif mode == "replay":
            path = recording_path(cid, recordings)
            if not path.exists():
                return CaseResult(cid, "fail", failures=[f"no recording {path} (run: record --source mock|live)"])
            client = JsonlReplayClient.from_file(path)
            models = tuple(client.header.get("models") or models)  # type: ignore[assignment]
            if client.header.get("prompts") != prompts_hash(spec):
                res.notes.append("recording predates current prompts (re-record with --source live)")
        else:
            raise ValueError(f"unknown mode {mode!r}")
    elif isinstance(client, RecordingClient) and not isinstance(client.inner, MockLLM):
        models = (extract_model(), phrase_model())
    extractor = Extractor(client, spec, model=models[0])
    phraser = Phraser(client, spec, model=models[1])

    initial = initial_state(spec, cid, binding.get("setup") or {})
    state = initial
    traces: list[TurnTrace] = []
    plans: list[Any] = []
    last_reply: Optional[str] = None

    def turn(step: dict) -> None:
        nonlocal state, last_reply
        before = copy.deepcopy(state)
        kw: dict[str, Any] = {}
        label = step.get("utterance") or step.get("action", "")
        if "utterance" in step:
            kw["utterance"] = step["utterance"]
        elif step["action"] in ("oauth_success", "oauth_success_other_account"):
            kw.update(oauth_verified=True, oauth_email=binding.get("oauth_email", "someone@gmail.com"))
        else:
            raise ValueError(f"{cid}: action {step['action']!r} is not drivable in the convo tier")
        retries = 1 if binding.get("client_retry") else 0
        while True:
            out = run_turn(spec, state, channel=CHANNEL, extractor=extractor, phraser=phraser,
                           last_assistant=last_reply, **kw)
            if state != before:
                res.failures.append(f"{label!r}: run_turn mutated its input state")
            intents = list(out.extraction.extraction.intents) if out.extraction and out.extraction.ok else []
            res.turns.append(TurnLog(label, out.reply, out.result.state.node, intents, out.extraction_failed))
            if not (out.reply or out.result.plan.absorbed):
                res.failures.append(f"{label!r}: dead air (empty reply)")
            if out.extraction_failed:
                if out.result.state != before:
                    res.failures.append(f"{label!r}: failed extraction changed state")
                if retries:
                    retries -= 1
                    res.notes.append(f"{label!r}: extraction failed ({out.extraction.error}); client retried once")
                    continue
            break
        traces.append(TurnTrace(before.node, out.result.state.node,
                                [s for s in spec.slots if before.filled(s)], out.result.plan.ask, intents))
        plans.append(out.result.plan)
        state, last_reply = out.result.state, out.reply

    expected = case["expected"]["state"]
    try:
        for step in script_steps(case, binding):
            turn(step)
        confirm_phase = any(v == THEN_CONFIRM for v in expected.values())
        checks: list[Check] = check_state(spec, expected, state, initial,
                                          phase="script" if confirm_phase else "final")
        for i, want in enumerate(binding.get("expect_plans") or []):
            if i >= len(plans):
                checks.append(Check(f"plan[{i}]", False, want, "no such turn"))
                continue
            checks += [Check(f"turn{i}.{c.key}", c.ok, c.want, c.got) for c in check_plan(want, plans[i])]
        if confirm_phase:
            for s in binding.get("confirm_steps") or []:
                turn({"utterance": s.get("type", s.get("say"))})
            checks += [Check(f"after_confirm.{c.key}", c.ok, c.want, c.got)
                       for c in check_state(spec, {k: v for k, v in expected.items() if v == THEN_CONFIRM},
                                            state, initial, phase="final")]
    except BaseException as e:  # FixtureError / replay drift are BaseException on purpose
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        res.status = "fail"
        res.failures.append(f"{type(e).__name__}: {e}")
        return res

    state_checks = [c for c in checks if "." not in c.key or c.key.startswith("after_confirm.")]
    sc = slot_correctness(state_checks)
    ot, ot_notes = on_track(spec, traces)
    res.scores = {"slot_correctness": sc, "on_track": ot}
    res.failures += [f"{c.key}: want {c.want!r} got {c.got!r}" for c in checks if not c.ok]
    res.failures += ot_notes
    remaining = getattr(client, "remaining", 0)
    if remaining:
        res.failures.append(f"{remaining} recorded responses were not consumed (replay drift)")
    if res.failures:
        res.status = "fail"
    return res


def record_case(spec: FlowSpec, case: dict, binding: dict, source: str,
                recordings: Path = RECORDINGS) -> CaseResult:
    if binding.get("pending"):
        return run_case(spec, case, binding, "mock")
    if source == "live":
        if os.environ.get(LIVE_FLAG) != "1":
            raise SystemExit(f"refusing to call the API: set {LIVE_FLAG}=1 to record (costs money)")
        from agent.llm.client import make_client
        inner: Any = make_client()
        models = [extract_model(), phrase_model()]
    elif source == "mock":
        inner = _mock(spec, binding)
        models = [DEFAULT_EXTRACT_MODEL, DEFAULT_PHRASE_MODEL]
    else:
        raise ValueError(f"unknown record source {source!r}")
    rec = RecordingClient(inner)
    res = run_case(spec, case, binding, "record", client=rec)
    header = {"case": case["id"], "provenance": "live" if source == "live" else "synthetic",
              "about": ("Recorded from the real Anthropic API." if source == "live" else
                        "SYNTHETIC: generated from MockLLM fixtures in harness/convo/cases.yaml "
                        "(Anthropic Messages shape, not live model output)."),
              "models": models, "flow_version": spec.raw["version"], "prompts": prompts_hash(spec)}
    rec.write(recording_path(case["id"], recordings), header)
    return res


def run_all(mode: str, *, case_ids: Optional[list[str]] = None, backend: Optional[EvalBackend] = None,
            record_source: Optional[str] = None, recordings: Path = RECORDINGS) -> dict:
    spec = load_spec()
    catalog = load_catalog()
    cases = [(c, b) for c, b in convo_cases(catalog) if not case_ids or c["id"] in case_ids]
    guard = NetworkGuard()
    offline = record_source != "live"
    results: list[CaseResult] = []
    with (guard.active() if offline else contextlib.nullcontext(guard)):
        for c, b in cases:
            if record_source:
                results.append(record_case(spec, c, b, record_source, recordings))
            else:
                results.append(run_case(spec, c, b, mode, recordings=recordings))
    name = run_name(spec)
    meta = {"tier": TIER, "mode": record_source and f"record-{record_source}" or mode,
            "flow_version": spec.raw["version"], "prompts": prompts_hash(spec),
            "network_attempts": guard.attempts,
            "pending": {r.case_id: r.summary() for r in results if r.status == "pending"}}
    if backend is not None and not record_source:
        backend.sync_dataset(catalog["dataset"], [c for c, _ in cases])
        items = [ItemResult(r.case_id, f"convo-{r.case_id}", r.scores, r.summary())
                 for r in results if r.status != "pending"]
        backend.record_run(name, catalog["dataset"], items, meta)
    ok = not any(r.status == "fail" for r in results) and not guard.attempts
    return {"run": name, "ok": ok, "results": results, "metadata": meta}


def default_backend(mode: str) -> LocalEvalBackend:
    # One directory per mode so mock and replay runs of the same build never overwrite.
    return LocalEvalBackend(EVALS_ROOT / f"{TIER}-{mode}")
