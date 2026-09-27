# harness/convo — scripted text conversations (qa:convo, packet HARNESS-001)

Runs each `convo`-tier case from `harness/edge-cases.yaml` against the agent's text
turn loop **in-process** (`agent.llm.run_turn`: extract -> `brain.apply` -> phrase +
guard; no HTTP), with the LLM replaced by (`services/agent/agent/llm/testing.py`):

- `mock`   : `MockLLM`, a deterministic extractor keyed by utterance (fixtures in
             `cases.yaml`); phrasing returns empty text so replies are the templates
- `replay` : `JsonlReplayClient` over `fixtures/recordings/<case>.jsonl`; every request
             must match the recorded kind + utterance (drift fails the case)

Both modes run behind a socket guard: any network attempt fails the run.

```
npm run qa:convo                                   # tests + mock run + replay run
python -m harness.convo run --mode mock|replay [--case EC-14]
python -m harness.convo record --source mock       # regenerate SYNTHETIC recordings
PERSONA_QA_LIVE=1 python -m harness.convo record --source live   # real API, costs money
python -m harness.convo runs --mode replay
python -m harness.convo compare '<run-a>' '<run-b>' --mode replay  # per-case deltas; exit 1 on regression
```

- `edge-cases.yaml` owns scripts and `expected.state`; `cases.yaml` binds each case to a
  structured setup, mock extractions, placeholder overrides, faults (EC-07 timeout),
  per-turn plan checks, or `pending: <reason>`.
- Scores per case (deterministic): `slot_correctness` (share of `expected.state` keys
  met) and `on_track` (no re-ask of a filled slot; node never moves back except on
  `change_answer`). A case passes only at 1.0/1.0 with every plan check met and no
  dead-air turn.
- Each run is recorded through `harness/evals/interface.py` (`LocalEvalBackend`, in
  `.persona-qa/evals/convo-<mode>/`) as `<git-sha>|flow-v<N>|prompts-<hash>`; the
  prompts hash covers the extraction/phrasing system prompts + the `record_slots` schema.
- Checked-in recordings are **synthetic** (from `MockLLM`, `provenance: synthetic` in
  each header). Re-record with `--source live` when prompts change; a replay whose
  header hash differs from current prompts gets a note.

The live tier (`qa:live`, OBS-002) runs the same scripts with the real LLM and adds
LLM-judge scores.
