"""Thin in-house eval interface. Harness code imports ONLY this module.

Backends:
  - LocalEvalBackend   : writes runs/scores to .persona-qa/evals/*.json (offline default)
  - LangfuseEvalBackend: harness/evals/langfuse_backend.py (packet OBS-002); the only
                         harness file allowed to import `langfuse`.

Model:
  dataset  = harness/edge-cases.yaml (synced as Langfuse dataset "persona-onboarding-edge-cases";
             one item per case id; input = setup+script, expected_output = expected)
  run      = one execution of the dataset against a build, named
             "<git-sha>|flow-v<version>|prompts-<hash>" so prompt/flow changes compare cleanly
  scores   = slot_correctness (deterministic: final slot statuses/values vs expected.state)
             on_track         (deterministic node-progress check + LLM-judge 0..1)
             no_fabrication   (LLM-judge 0..1 + deterministic: bot never asserts a slot value
                               or capability not present in state/allow-list)
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

SCORES = ("slot_correctness", "on_track", "no_fabrication")


@dataclass
class ItemResult:
    case_id: str
    trace_id: str | None
    scores: dict[str, float] = field(default_factory=dict)
    notes: str = ""


class EvalBackend(Protocol):
    def sync_dataset(self, name: str, cases: list[dict]) -> None: ...
    def record_run(self, run_name: str, dataset: str, results: list[ItemResult], metadata: dict) -> None: ...
    def compare(self, run_a: str, run_b: str) -> dict[str, dict[str, float]]: ...


class LocalEvalBackend:
    def __init__(self, root: Path | str = ".persona-qa/evals"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def sync_dataset(self, name: str, cases: list[dict]) -> None:
        (self.root / f"dataset-{name}.json").write_text(json.dumps(cases, indent=2, default=str))

    def record_run(self, run_name: str, dataset: str, results: list[ItemResult], metadata: dict) -> None:
        safe = run_name.replace("|", "_").replace("/", "_")
        doc = {"run": run_name, "dataset": dataset, "at": time.time(), "metadata": metadata,
               "results": [asdict(r) for r in results]}
        (self.root / f"run-{safe}.json").write_text(json.dumps(doc, indent=2))

    def compare(self, run_a: str, run_b: str) -> dict[str, dict[str, float]]:
        def load(r: str) -> dict[str, dict[str, float]]:
            doc = json.loads((self.root / f"run-{r.replace('|', '_').replace('/', '_')}.json").read_text())
            return {x["case_id"]: x["scores"] for x in doc["results"]}
        a, b = load(run_a), load(run_b)
        return {cid: {s: b.get(cid, {}).get(s, 0.0) - a[cid].get(s, 0.0) for s in SCORES} for cid in a}
