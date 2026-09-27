# packages/flow — the flow spec

`flow.yaml` is the state machine **as data**: slots, per-channel ask order, retry
budgets, graduation rules, intents, nodes, and per-node tool scoping.

## Ownership decision

**Single runtime owner: Python (`services/agent/agent/brain`).** Both channels call it:

- **Voice**: the Pipecat pipeline (same Python service) builds Pipecat Flows
  `NodeConfig`s from this spec; every node's function handlers delegate to
  `brain.apply(...)`, which returns the next node.
- **Text**: `apps/web` calls the agent HTTP API (`POST /v1/sessions/{id}/turns`);
  the API runs the *same* brain with direct Anthropic SDK calls.

Why not "two engines reading one spec"? Two implementations of transition logic
drift, and every edge case would need to be tested twice. Pipecat Flows is Python,
so the voice side must be Python anyway; making text call the same engine gives
one brain, one state, one test suite. Cost: text chat depends on the agent service
being up (mitigated by health checks, retries, and a clear reconnect UI). TS only
consumes generated types (`npm run flow:types`, planned in FLOW-001) for labels and
slot names; it never decides transitions.

## Invariants (enforced by `services/agent/tests/test_spec.py`)

- every node is reachable from `greet`; `graduated` is the only terminal
- every `collect` node names a slot that exists; every slot has a collect node
- `agent_name` is text-only and never appears in the voice ask order
- every tool name is in the brain's tool registry
- per-node tool scoping: only `gmail` may push the connect button or capture email
