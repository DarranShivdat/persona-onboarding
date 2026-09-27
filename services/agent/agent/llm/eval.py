"""Extraction model eval: score candidate models on tests/fixtures/llm/extract_cases.json.

Offline (default, no network): replays recordings under
  tests/fixtures/llm/recordings/<model>/<case_id>.json   ({"response": ..., "latency_ms": ...})
through the real adapter parse path and prints slot/intent accuracy + latency.

Record (costs money; explicit flag only):
  PERSONA_QA_LIVE=1 python -m agent.llm.eval --record --models claude-haiku-4-5,claude-sonnet-5

Usage (from services/agent):  python -m agent.llm.eval [--models a,b]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Optional

from ..brain.engine import Extraction
from ..brain.spec import FlowSpec, load_spec
from .client import ReplayClient, make_client, to_jsonable
from .extract import Extractor

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "llm"
CASES_PATH = FIXTURES / "extract_cases.json"
RECORDINGS = FIXTURES / "recordings"
CANDIDATES = ("claude-haiku-4-5", "claude-sonnet-5")
LIVE_FLAG = "PERSONA_QA_LIVE"


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    return json.loads(path.read_text())["cases"]


def _norm(v: Optional[str]) -> str:
    return " ".join((v or "").casefold().strip(" .!?\"'").split())


def score_case(spec: FlowSpec, case: dict, x: Extraction) -> dict[str, Any]:
    exp = case["expect"]
    want = exp.get("slots", {})
    optional = set(exp.get("optional_slots", []))
    slot_ok = True
    for name in spec.slots:
        got = x.slots.get(name)
        if name in want:
            # need is free text: accept if the expected phrase is contained.
            ok = _norm(want[name]) == _norm(got) or (name == "need" and _norm(want[name]) in _norm(got))
        else:
            ok = got is None or name in optional
        slot_ok &= ok
    got_i = set(x.intents)
    need_i = set(exp.get("intents", []))
    alt = set(exp.get("alt_intents", []))
    intent_ok = (need_i <= got_i or bool(alt & got_i)) and not (set(exp.get("forbid_intents", [])) & got_i)
    if not need_i and not exp.get("forbid_intents"):
        intent_ok = not (got_i & {"refuse_slot", "insist_graduate", "prompt_injection", "change_answer"}) or bool(alt & got_i)
    return {"id": case["id"], "slots_ok": slot_ok, "intents_ok": intent_ok, "ok": slot_ok and intent_ok}


def evaluate(spec: FlowSpec, model: str, cases: list[dict], recordings: dict[str, dict]) -> dict[str, Any]:
    rows, lat = [], []
    for case in cases:
        rec = recordings.get(case["id"])
        if rec is None:
            rows.append({"id": case["id"], "ok": False, "slots_ok": False, "intents_ok": False, "missing": True})
            continue
        ex = Extractor(ReplayClient([rec["response"]]), spec, model=model)
        res = ex.extract(case["utterance"], case["context"])
        rows.append(score_case(spec, case, res.extraction) | {"tool_call": res.ok})
        if rec.get("latency_ms") is not None:
            lat.append(float(rec["latency_ms"]))
    n = len(rows) or 1
    return {
        "model": model,
        "cases": len(rows),
        "recorded": sum(1 for r in rows if not r.get("missing")),
        "accuracy": round(sum(r["ok"] for r in rows) / n, 3),
        "slot_accuracy": round(sum(r["slots_ok"] for r in rows) / n, 3),
        "intent_accuracy": round(sum(r["intents_ok"] for r in rows) / n, 3),
        "p50_ms": round(statistics.median(lat), 1) if lat else None,
        "p90_ms": round(sorted(lat)[int(0.9 * (len(lat) - 1))], 1) if lat else None,
        "failures": [r["id"] for r in rows if not r["ok"]],
    }


def load_recordings(model: str, root: Path = RECORDINGS) -> dict[str, dict]:
    d = root / model
    if not d.is_dir():
        return {}
    return {p.stem: json.loads(p.read_text()) for p in sorted(d.glob("*.json"))}


def record(spec: FlowSpec, model: str, cases: list[dict], root: Path = RECORDINGS) -> None:
    if os.environ.get(LIVE_FLAG) != "1":
        raise SystemExit(f"refusing to call the API: set {LIVE_FLAG}=1 to record (costs money)")
    client = make_client()
    ex = Extractor(client, spec, model=model)
    out = root / model
    out.mkdir(parents=True, exist_ok=True)
    for case in cases:
        t0 = time.perf_counter()
        resp = client.messages.create(**ex.request(case["utterance"], case["context"]))
        ms = round((time.perf_counter() - t0) * 1000, 1)
        (out / f"{case['id']}.json").write_text(json.dumps(
            {"provenance": "live", "model": model, "latency_ms": ms, "response": to_jsonable(resp)}, indent=2))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", default=",".join(CANDIDATES))
    ap.add_argument("--record", action="store_true")
    a = ap.parse_args(argv)
    spec, cases = load_spec(), load_cases()
    for m in [m.strip() for m in a.models.split(",") if m.strip()]:
        if a.record:
            record(spec, m, cases)
        print(json.dumps(evaluate(spec, m, cases, load_recordings(m))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
