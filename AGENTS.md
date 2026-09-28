# Persona Onboarding — Agent Orchestration

Adapted from Darran's FLOAT `AGENTS.md` (same EM model, same supervisor, same
packet discipline). Read `CLAUDE.md`, `docs/ARCHITECTURE.md`, and
`docs/ROADMAP.md` before assigning work. This file defines orchestration only.

## Operating goal

**Maximize** autonomous engineering progress per unit of scarce Claude allocation.
**Minimize** Darran's involvement. Never save Claude allocation by pushing testing
or engineering onto Darran.

> Darran → Grok (Engineering Manager) → cheapest capable worker → independent
> automated QA → autonomous fix/retest → integration → design-review gate → next dependency

## Human role (D — Darran)

Product owner and subjective reviewer. Darran handles only: conversational feel
("does it feel like a conversation, not a form?"), visual taste, product/scope
decisions, anything touching Persona's evaluation criteria, and account-level
actions (Google OAuth test users, vendor keys, deploy/billing). Darran is not QA.

Before asking Darran anything, exhaust automated validation (qa tiers, edge-case
scripts, visual diff, live surface check). Ask exactly **one** primary subjective
question per review.

## Lead agent (A — Grok)

Grok is the persistent EM: state, decomposition, classification (A/B-low/B-high/C/D),
dependencies, worktrees/Git, packets, supervisor reconciliation, QA routing,
integration, design-review gate, and deciding when human judgment is needed.
Top-level orchestration never moves to Claude.

## Work classes (cheapest capable)

| Class | Who | Use for |
|---|---|---|
| **A** | Grok / automation | Orchestration, Git, inspection, qa tiers, harness runs, log/trace analysis, Langfuse run comparison, integration, docs/status, design-review gate, live-surface verification |
| **B-low** | Grok Build | Bounded, low-risk, objectively testable changes (config, harness plumbing, docs, small existing-pattern components, straightforward tests) |
| **B-high** | Opus 5.5 via `./scripts/claude-worker.sh opus [--role ...]` | Substantive implementation: brain/engine, extraction & validation, voice pipeline, failover, reconnect/lease logic, OAuth, UI from spec, design research/spec |
| **C** | Fable via `./scripts/claude-worker.sh fable` | Rare: real-time concurrency bugs across voice/text, WebRTC/ICE hosting failures, repeated Opus failure, deep architecture review |
| **D** | Darran | Feel, taste, product/scope, accounts/keys |

Scarcity rule: scarcity may move a borderline B-low to Grok; it must **never** move
genuine B-high/C work down. If the right worker is unavailable (exit 75), defer and
continue only dependency-valid work. No filler work.

## Worker roles (all Claude Code Opus workers, same supervisor + packets)

| Role | Flag | Writes | Must not | Packet template |
|---|---|---|---|---|
| **impl** (default) | `--role impl` | code in packet SCOPE (`services/`, `packages/`, `harness/`, `infra/`) | touch `docs/design/` specs | `docs/orchestration/packets/TEMPLATE.impl.md` |
| **DESIGN** | `--role design` | `docs/design/**` only: research notes, reference screenshots, `spec.md`, static mockups (HTML/CSS + PNG renders) | write production code in `apps/` or `services/`; commit third-party brand assets outside `docs/design/references/` | `docs/orchestration/packets/TEMPLATE.design.md` |
| **FRONTEND IMPLEMENTER** | `--role frontend` | `apps/web/**`, `harness/visual/**` | change `docs/design/spec.md` (raise a spec question instead); add transition logic to the browser | `docs/orchestration/packets/TEMPLATE.frontend.md` |

- DESIGN workers use `.claude/skills/frontend-design` (+ `webapp-testing` for
  Playwright capture). They research Persona's public product/onboarding UI, capture
  screenshots into `docs/design/references/`, and deliver `docs/design/spec.md`
  (flow, layout, type scale, color tokens, spacing, motion, component states for chat,
  phone simulator, Gmail connect card, graduation) plus mockups in `docs/design/mockups/`
  (one PNG per key state at 1440×900 and 390×844, named to match `harness/visual`).
- FRONTEND workers implement a **frozen** spec revision (packet cites the spec commit
  SHA), verify with Playwright flow tests (`apps/web/e2e`) and screenshot diffs against
  the mockups (`npm run qa:visual`), and report diff scores per state.
- Spec changes after freeze go through a new DESIGN packet (or Darran, if taste).

## Mandatory preflight (before every Claude call)

Grok writes a packet from the matching template. Required fields:

```
TASK / WHY / SCOPE / READ / DO NOT READ / REQUIREMENTS / ACCEPTANCE /
CONSTRAINTS / OUTPUT / BUDGET
+ design role:   REFERENCES / DELIVERABLES
+ frontend role: SPEC (path@sha) / VISUAL_ACCEPTANCE (states + max diff)
```

Launch: `./scripts/claude-worker.sh opus --role <role> --packet docs/orchestration/packets/<ID>.md`
(or `PERSONA_WORKER_PACKET=...`). The wrapper warns on missing fields per role.
Model: `claude-opus-5-5` by default (`PERSONA_OPUS_MODEL` overrides). Before the
first real worker, run `./scripts/claude-worker.sh opus --probe-model` once.
From an agent/tool shell the launch auto-detaches (new session) and survives the launching run;
see LOCAL-SUPERVISOR.md "Detach". Never background it with `&`.

Every packet's DO NOT READ includes: `.env*`, secrets, and **Penciled sensitive paths**
(`transcripts/`, `data/`, demo patient info, credentials, anything with PHI) plus other
Penciled repos. Penciled voice-agent code is Darran's IP and **may be copied** into this
repo — read it from the sanitized read-only mirror `/Users/darranshivdat/IdeaProjects/persona-onboarding-ref/penciled-voice-agent/`.
**Never modify penciled-emr.** Enforced for every worker by `.claude/settings.json`
(deny rules) + `scripts/guard-tool-use.py` (PreToolUse hook, exit 2 = block; tested in
`harness/tests/test_guard_hook.py`). Workers must not route around a block.

## Budgets and supervision (unchanged from FLOAT)

- Default hard caps: Opus 40 turns / Fable 60. Raise per-task only with a recorded reason.
- Local supervisor (`./scripts/persona-supervisor.sh`, `docs/orchestration/LOCAL-SUPERVISOR.md`)
  owns lifecycle: PIDs, stream parsing, checkpoints at 40/70/85%, finish-mode before the
  hard cap, STALE detection, bounded recovery (max 2), worktree locks, `caffeinate`,
  heartbeat, `.persona-worker/events.jsonl`.
- Exit 75 = RATE_LIMITED (defer; never downgrade). Exit 76 = HARD_CAP (preserve worktree;
  never reset; Grok inspects → narrow finish packet).
- Classifications: HEALTHY / NEAR_FINISH / WANDERING / BLOCKED / OVERSIZED / STALE.
- A single RPC failure to the Mac is `COMPUTER_RPC_UNAVAILABLE`, not "Mac offline";
  prefer the supervisor heartbeat.
- One editing worker per worktree (`../persona-onboarding-worktrees/<slug>`, branch
  `claude/<slug>`). Never two editors on the same area concurrently.

## Default engineering loop

1. Darran gives direction → Grok classifies and plans dependencies (`docs/ROADMAP.md`).
2. Grok (A/B-low) or worker (B-high/C) implements from a packet.
3. Grok integrates onto `main` locally (no remote until Darran approves hosting).
4. QA by risk (never ask Darran):
   - `npm run qa:fast` — every change
   - `npm run qa:flow` + `qa:convo` — brain / prompt / extraction changes
   - `npm run qa:voice` — anything in the voice path
   - `npm run qa:e2e` + `qa:visual` — anything in `apps/web`
   - `PERSONA_QA_LIVE=1 npm run qa:live` — prompt/flow changes before READY; compare the
     new eval run against the previous one (Langfuse run comparison) and block on
     regressions in `slot_correctness`, `on_track`, or `no_fabrication`.
5. On FAIL: diagnose → right worker fixes → re-QA.
6. **Design-review gate** (below) for anything user-visible.
7. **Live test-surface verification** (below) → READY.

## Design-review gate (A-class, mandatory before READY for any UI change)

1. Identify the target: the Vercel preview URL for the branch (or local `next start`
   from the correct worktree before hosting exists). Confirm build identity (commit SHA
   rendered in a `<meta name="build-sha">` tag).
2. `npm run qa:visual -- --url <target>`: capture every spec state at desktop and
   mobile widths; pixel-diff against `docs/design/mockups/`; write
   `.persona-qa/visual/report.json` and a side-by-side contact sheet.
3. Grok reviews the contact sheet against the `docs/design/spec.md` acceptance
   checklist (layout, type scale, color tokens, component states, motion notes,
   copy tone) and records PASS/FAIL per state in the report.
4. Thresholds: per-state diff ≤ the packet's VISUAL_ACCEPTANCE (default 3% of pixels
   after masking dynamic regions like timers/captions). Above threshold → FE fix
   packet with the diff images attached; spec mismatch that is actually a spec gap →
   DESIGN packet.
5. Only after PASS may the change be presented to Darran, with the contact sheet.

## Live test-surface verification (A-class, before READY)

Immediately before handing Darran a URL: the web app and agent service are running
from the intended commit; the URL responds; `/health` is green; a scripted text
turn round-trips; a scripted voice call connects and hears the greeting; tracing
is emitting; no console errors on fresh load. Never hand over a dead URL.

## Return to Darran only for

- **READY FOR PRODUCT TEST** — QA green, design gate passed, surface verified; one
  subjective question; exact steps.
- **PRODUCT DECISION REQUIRED** — ≤3 options, evidence, recommendation.
- **TRUE EXTERNAL BLOCKER** — keys, accounts, OAuth test users, billing, persistent
  rate limit with no dependency-valid work left.
