# harness — test harness design

Every one of the 31 cases in `edge-cases.yaml` is owned by ≥1 tier (enforced by
`harness/tests/test_edge_case_catalog.py`).

| Tier | Command | What | Status |
|---|---|---|---|
| (a) flow | `npm run qa:flow` | Pure state-machine tests over `agent/brain` — deterministic, no LLM, no I/O. Spec invariants run in `qa:fast`. | spec invariants live; engine tests PENDING (FLOW-001) |
| (b) convo | `npm run qa:convo` | Scripted text conversations through the real turn loop with a mocked or **replayed** (recorded) Anthropic LLM; asserts final state. | PENDING (HARNESS-001) |
| (b') live | `PERSONA_QA_LIVE=1 npm run qa:live` | Same scripts, real LLM, **LLM judge**; results recorded as an eval run. | PENDING (OBS-002) |
| (c) voice | `npm run qa:voice` | Headless Pipecat client streams TTS-generated audio and injects hangup / network drop / silence / barge-in / vendor failure on a timeline. Asserts state, time-to-first-audio, max dead air, no double-emit. | PENDING (HARNESS-003) |
| e2e | `npm run qa:e2e` | Playwright UI flows (fake media devices for voice). | PENDING (FE-001) |
| visual | `npm run qa:visual -- --url <u>` | Screenshot capture + pixel diff vs `docs/design/mockups` (design-review gate). | PENDING (FE-001) |
| harness | `npm run qa:harness` | Supervisor mock harness (orchestration, no Claude). | live |

## Evals with Langfuse (behind `harness/evals/interface.py`)

- The catalog **is** the dataset: `sync_dataset("persona-onboarding-edge-cases", cases)`.
- Each live run is named `<git-sha>|flow-v<N>|prompts-<hash>` and records per-case
  scores: `slot_correctness` (deterministic vs `expected.state`), `on_track`
  (deterministic progress + judge), `no_fabrication` (judge + deterministic claim check
  against state and `docs/product-facts.md`). Rubric: `evals/judge-rubric.yaml`.
- `compare(prev_run, new_run)` gives per-case deltas → EM blocks READY on regressions.
- Offline default is `LocalEvalBackend` (`.persona-qa/evals/`), so nothing requires
  Langfuse to run; the Langfuse backend is a drop-in (only file importing `langfuse`).
- Agent traces (`agent/obs`) carry the same run/case ids so a failing score links to the
  exact trace in Langfuse.

Reports: `.persona-qa/last-report.json` (host + cwd recorded for authority).

## qa:audit — button audit (AUDIT-001)

`npm run qa:audit` runs `apps/web/playwright.audit.config.ts`: every control on every screen,
desktop + mobile, against the LOCAL stub (offline, ~1 min; web :3420, stub :3219). Unknown
controls fail. Set `PERSONA_AUDIT_URL=<web url>` for the LIVE target and `PERSONA_AUDIT_DOCS=1` to
write `docs/qa/button-audit.md` + screenshots. It is part of `npm run qa` and gates
`scripts/deploy/{vercel-web,fly-agent}.sh --apply` (`--skip-audit` overrides loudly);
`scripts/deploy/smoke.sh <web> <agent> --audit` runs it LIVE after a deploy. See docs/qa/button-audit.md.
