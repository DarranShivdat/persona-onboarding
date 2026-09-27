# CLAUDE.md — rules for Claude Code workers in this repo

## What this is
A hosted, conversational onboarding for an AI assistant (Persona CTO trial). It
collects: (1) agent name (text, before the call), (2) the user's name, (3) a
connected Gmail, (4) one thing they need help with — via adaptive text chat **and**
a browser "phone call" (voice) that collects everything except the agent name.
Reviewers will stress-test it (hangups, refusals, nonsense). It must feel like a
conversation, steer back gently, and allow early graduation.

## Stack (decided — don't relitigate in a worker)
- `apps/web`: Next.js on Vercel (chat + phone simulator + Gmail card + graduation).
- `services/agent`: Python. `agent/brain` = pure flow engine (the only runtime that
  executes `packages/flow/flow.yaml`); `agent/voice` = Pipecat pipeline (SmallWebRTC,
  Deepgram STT, Claude, Cartesia TTS w/ failover, barge-in) using **Pipecat Flows**
  nodes generated from the spec; `agent/api` = text turn API.
- LLM: **direct Anthropic SDK** (and Pipecat's Anthropic service on voice).
  **No LangChain / LangGraph.**
- Tracing/evals: **Langfuse**, only behind `agent/obs/tracing.py` and
  `harness/evals/interface.py`. Only the adapter files may `import langfuse`.
- Postgres (Supabase) is the single source of truth; the agent is the single writer.

## Hard invariants
1. **Code owns progress; the LLM owns phrasing and extraction.** No LLM output may
   change node/slot state except through validated `record_slots` extractions.
2. Every utterance runs extraction for **all** slots; out-of-order answers fill and skip.
3. A slot is `filled` only after its validator passes. Gmail is `filled` only via OAuth.
4. Graduation is legal from any node once `need` is filled or the user insists.
5. Per-node tool scoping as declared in `flow.yaml` (tested).
6. One brain, one state for text and voice. Hangup never loses more than the in-flight turn.
7. Never dead air on a call: every node has a spoken silence floor.
8. No transition logic in the browser.

## Absolute rules
- **No Penciled code.** Never open, copy, or paraphrase files from any Penciled
  repository (penciled-emr, penciled-dev, etc.). `docs/penciled-reference-map.md`
  describes *patterns* in prose; reimplement from Pipecat's public docs.
  (Darran may later lift this in writing; until then it stands.)
- **Secrets**: never read `.env*`, key files, or credential stores; never print or
  commit secrets; reference env var *names* only (`.env.example`).
- **No pushes.** Commit locally on your branch; never `git push`, never add remotes.
- No new runtime dependency outside your packet SCOPE without saying so in OUTPUT.

## Commands
```
npm run qa:fast      # flow spec + edge-case catalog (seconds)
npm run qa:flow      # pure engine tests
npm run qa:convo     # scripted text convos (mock/replay LLM)
npm run qa:voice     # headless voice client + fault injection
npm run qa:e2e       # Playwright
npm run qa:visual    # screenshot diff vs docs/design/mockups
npm run qa:harness   # supervisor mock harness (no Claude)
PERSONA_QA_LIVE=1 npm run qa:live   # real LLM + judge -> eval backend (costs money)
```
Python: `PERSONA_PYTHON` selects the interpreter (needs pyyaml + pytest for fast/flow).

## Definition of done (every packet)
- ACCEPTANCE commands in the packet pass locally; `npm run qa:fast` passes.
- New behavior is covered by a test in the right tier; any edge case touched has its
  catalog entry's test implemented (remove the PENDING skip).
- No secrets, no Penciled code, no browser-side transition logic.
- Small commits, one concern each, conventional prefix (`feat(brain): ...`).
- Final report in the wrapper's STATUS format, including residual risks.
