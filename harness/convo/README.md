# harness/convo — scripted text conversations (qa:convo, packet HARNESS-001)

Runs each `convo`-tier case from `harness/edge-cases.yaml` against the agent's text
turn loop **in-process** (no HTTP), with the LLM replaced by:

- `mock`   : deterministic fake extractor/phraser keyed by utterance (fixtures/*.json)
- `replay` : recorded Anthropic responses (fixtures/recordings/<case>.jsonl), re-recorded
             with `--record` against the real API when prompts change

Assertions: `expected.state` (deterministic). The live tier (`qa:live`) runs the same
scripts with the real LLM and adds LLM-judge scores, recorded through
`harness/evals/interface.py` (Local backend offline; Langfuse backend when configured).
