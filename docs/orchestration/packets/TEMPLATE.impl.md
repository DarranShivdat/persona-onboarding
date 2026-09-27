# <ID> — <title>

CLASS: B-high | C
MODEL: opus | fable
ROLE: impl
WORKTREE: ../persona-onboarding-worktrees/<slug>
BRANCH: claude/<slug>
DEPENDS ON: <packet ids>

## TASK
Exact change to make.

## WHY
Milestone / defect / dependency.

## SCOPE
Files/directories you may edit. Anything else: document the narrow expansion in OUTPUT.

## READ
- CLAUDE.md
- docs/ARCHITECTURE.md §<n>
- <exact files>

## DO NOT READ
- .env*, secrets, credential stores
- any Penciled repository (penciled-emr, penciled-dev, ...)
- unrelated packets, agent transcripts, broad git archaeology

## REQUIREMENTS
- Concrete expected behavior …

## ACCEPTANCE
- [ ] `npm run qa:fast`
- [ ] `<named tests / tiers>`
- [ ] edge cases implemented (remove PENDING): EC-..

## CONSTRAINTS
Invariants from CLAUDE.md that apply; no browser transition logic; no LangChain; Langfuse only via adapters.

## OUTPUT
Commit on the branch (no push). Report STATUS / FILES_CHANGED / TESTS / COMMIT / RISKS / PRODUCT_DECISION_REQUIRED.

## BUDGET
Opus 40 turns (default).
