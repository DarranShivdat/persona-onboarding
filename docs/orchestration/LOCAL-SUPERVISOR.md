# Persona local worker supervisor

Ported from FLOAT's `docs/orchestration/LOCAL-SUPERVISOR.md` +
`scripts/float-supervisor.py` (Darran's own harness; renamed `FLOAT_*` → `PERSONA_*`,
`.float-worker` → `.persona-worker`). Local Mac harness that supervises Opus/Fable
workers with live stream state, checkpoints, finish-mode continuations, stale
detection, and `caffeinate`. Not a cloud platform: Mac PIDs, local worktrees, the
local Claude CLI (or the mock worker in tests).

## Quick start

```bash
./scripts/persona-supervisor.sh start            # daemon; writes .persona-worker/supervisor.json
./scripts/persona-supervisor.sh start --foreground
./scripts/persona-supervisor.sh status | reconcile | stop
```

Launch a worker (the supervisor picks it up from state files):

```bash
./scripts/claude-worker.sh opus --role impl     --packet docs/orchestration/packets/FLOW-001.md
./scripts/claude-worker.sh opus --role design   --packet docs/orchestration/packets/DESIGN-001.md
./scripts/claude-worker.sh opus --role frontend --packet docs/orchestration/packets/FE-001.md
./scripts/claude-worker.sh opus --probe-model   # one tiny real call: confirms the model id is served
```

State directory (default `$REPO_ROOT/.persona-worker`, override `PERSONA_WORKER_STATUS_DIR`; gitignored):

```
.persona-worker/
  supervisor.json        # heartbeat, caffeinate pid
  events.jsonl           # CHECKPOINT_40/70/85, FINISH_TRANSITION, WORKER_STALE, WORKER_HARD_CAP, ...
  <worker-id>.json       # includes role, model, budget_pct, git dirty summary
  <worker-id>.stream.jsonl
  <worker-id>.stderr.log
  locks/                 # per-worktree locks
  packets/               # finish packets written by the supervisor
```

## Exit codes (worker)

| Code | Meaning |
|---|---|
| 0 | Success |
| 75 | Rate limited — defer B-high/C; never downgrade |
| 76 | Hard turn cap — preserve worktree; never reset |

## Connectivity semantics (for the Grok EM)

`CONNECTED` · `COMPUTER_RPC_UNAVAILABLE` (one RPC failed — not proof the Mac is
offline) · `LOCAL_SUPERVISOR_ALIVE` (heartbeat advancing) · `MAC_UNREACHABLE` ·
`UNKNOWN`. Workers keep running under the local supervisor while Grok is away;
Grok reconciles from `events.jsonl`.

## Behavior

Classifications: `HEALTHY`, `NEAR_FINISH`, `WANDERING`, `BLOCKED`, `OVERSIZED`,
`STALE`, `EXITED_SUCCESS`, `EXITED_FAILURE`, `HARD_CAP`, `INTERRUPTED`, `RATE_LIMITED`.
Checkpoints at 40/70/85% of the turn budget. Finish transition before the hard cap
(`PERSONA_FINISH_PCT`, default 82; stream-estimate turn counts need 98% + quiet git).
Finish-mode workers are never finish-thrashed. Max recovery continuations: 2.

## Acceptance

```bash
./scripts/test-persona-harness.sh     # mock only; no Claude allocation
npm run qa:harness                    # same, via the qa runner
```
Tests A–K are FLOAT's suite (normal run, near-cap finish transition, stall → STALE,
hard cap → preserve, parallel workers, supervisor survives EM absence, machine
restart reconcile, caffeinate lifecycle, event log validity, finish-mode anti-thrash).
Test L (new) covers roles and model resolution via `--dry-run`.

## Env knobs

`PERSONA_SUPERVISOR_POLL_SECONDS` (5) · `PERSONA_STALE_SECONDS` (720) ·
`PERSONA_FINISH_PCT` (82) · `PERSONA_MAX_RECOVERY` (2) · `PERSONA_OPUS_MODEL`
(`claude-opus-5-5`) · `PERSONA_FABLE_MODEL` (`claude-fable-5`) · `PERSONA_WORKER_ROLE` ·
`PERSONA_WORKER_MOCK=1` / `PERSONA_MOCK_MODE=...` · `PERSONA_CAFFEINATE_MOCK=1`.
