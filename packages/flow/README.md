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
consumes generated types (`npm run flow:types` -> `apps/web/lib/flow-types.ts`) for labels and
slot names; it never decides transitions.

## Invariants (enforced by `services/agent/tests/test_spec.py`)

- every node is reachable from `greet`; `graduated` is the only terminal
- every `collect` node names a slot that exists; every slot has a collect node
- `agent_name` is text-only and never appears in the voice ask order
- every tool name is in the brain's tool registry
- per-node tool scoping: only `gmail` may push the connect button or capture email

## Engine contract (`agent.brain.engine.apply`)

- Input: `Turn(channel, utterance, Extraction(slots, confidences, intents), oauth_verified, event)`.
  `event` is one of `open` (session start / return visit), `call_started`, `call_ended`.
  `oauth_verified=True` is set only by the OAuth callback; it is the only way gmail is `filled`.
- Text order: `greet -> agent_name -> call_offer -> first unresolved of ask_order.text`;
  voice uses `ask_order.voice` (never agent_name). The flow never walks back to `call_offer`.
- Retry ladder per node (`retry_budget`): reask -> explain_why -> skip (slot deferred).
  `refuse_slot` gets the `why` at most once, then is respected (`explain_on_first_ask`
  slots, e.g. gmail, skip on the first refusal).
- Confirms: joke/very long agent names and low-confidence voice names become
  `candidate` + `needs_confirm`; `affirm` fills, `deny` clears. Spelled names fill directly.
- Absorbed (no attempt): `noise_or_fragment` with no slot content; `prompt_injection`
  returns the input state unchanged; off_topic/privacy/other_language/abuse are
  answered then steered back.
- Graduation: `insist_graduate` from any node, or nothing left to ask; missing
  required slots become `deferred_prompts`. `value_demo` is voiced only when `need` is filled.
- Events (persisted in order): `intent`, `slot_filled`, `slot_candidate`, `slot_rejected`,
  `slot_changed`, `slot_cleared`, `slot_skipped`, `attempt`, `transition`, `channel`,
  `call_requested`, `absorbed`, `injection_ignored`, `event`, `graduated`.
