"""CLI:  python -m harness.convo <command>

  run     --mode mock|replay [--case EC-14 ...] [--no-eval]
  record  --source mock|live [--case ...]      (live needs PERSONA_QA_LIVE=1; costs money)
  compare RUN_A RUN_B [--mode mock|replay]      per-case score deltas
  runs    [--mode mock|replay]                  list recorded runs, oldest first

`run` prints one line per case and a final `CONVO_SUMMARY {json}` line (read by
harness/qa.mjs); exit 1 if any case fails or any network call was attempted.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import runner


def _run(a: argparse.Namespace) -> int:
    backend = None if a.no_eval else runner.default_backend(a.mode)
    out = runner.run_all(a.mode, case_ids=a.case, backend=backend)
    return _report(out, a.mode, backend is not None)


def _record(a: argparse.Namespace) -> int:
    out = runner.run_all("record", case_ids=a.case, record_source=a.source)
    print(f"[convo:record] wrote {runner.RECORDINGS.relative_to(runner.ROOT)}/<case>.jsonl ({a.source})")
    return _report(out, f"record-{a.source}", False)


def _report(out: dict, mode: str, recorded: bool) -> int:
    counts = {"pass": 0, "fail": 0, "pending": 0}
    for r in out["results"]:
        counts[r.status] += 1
        scores = " ".join(f"{k}={v:g}" for k, v in r.scores.items())
        print(f"[convo:{mode}] {r.status.upper():7} {r.case_id} {scores}" + (f" — {r.summary()}" if r.summary() else ""))
    net = out["metadata"]["network_attempts"]
    if net:
        print(f"[convo:{mode}] FAIL network calls attempted: {net}")
    summary = {"mode": mode, "run": out["run"], "ok": out["ok"], **counts, "network_calls": len(net),
               "recorded_eval": recorded}
    print("CONVO_SUMMARY " + json.dumps(summary, sort_keys=True))
    return 0 if out["ok"] else 1


def _compare(a: argparse.Namespace) -> int:
    b = runner.default_backend(a.mode)
    deltas = b.compare(a.run_a, a.run_b)
    print(f"compare {a.run_a} -> {a.run_b} ({runner.TIER}-{a.mode})")
    regressions = 0
    for cid, d in sorted(deltas.items()):
        moved = {k: v for k, v in d.items() if v}
        regressions += any(v < 0 for v in moved.values())
        print(f"  {cid}: " + (", ".join(f"{k} {v:+g}" for k, v in moved.items()) or "="))
    print(f"{regressions} case(s) regressed")
    return 1 if regressions else 0


def _runs(a: argparse.Namespace) -> int:
    for r in runner.default_backend(a.mode).list_runs():
        print(r)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness.convo", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--mode", choices=runner.MODES, default="mock")
    p.add_argument("--case", action="append")
    p.add_argument("--no-eval", action="store_true", help="do not record the run in the eval backend")
    p.set_defaults(fn=_run)
    p = sub.add_parser("record")
    p.add_argument("--source", choices=("mock", "live"), required=True)
    p.add_argument("--case", action="append")
    p.set_defaults(fn=_record)
    p = sub.add_parser("compare")
    p.add_argument("run_a")
    p.add_argument("run_b")
    p.add_argument("--mode", choices=runner.MODES, default="mock")
    p.set_defaults(fn=_compare)
    p = sub.add_parser("runs")
    p.add_argument("--mode", choices=runner.MODES, default="mock")
    p.set_defaults(fn=_runs)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
