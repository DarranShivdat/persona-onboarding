# NAME-001 — Get the user's name right (voice read-back + spelling fallback; text confirm when unusual)

CLASS: B-high
MODEL: opus
ROLE: impl
WORKTREE: ../persona-onboarding-worktrees/name-001
BRANCH: claude/name-001
DEPENDS ON: main @ HEAD (text Claude LLM 13ef15d+ merged)

## TASK
Darran's live call (2026-09-27, see docs/qa/live-test-2026-09-27.md) said "Darran"; Deepgram
heard "Darren" (confidence 0.95), the brain filled it with no confirmation, and every later
line + the graduation screen said "Darren". Fix name capture:
1. VOICE: after a user_name is captured on the call, ALWAYS read it back and ask to confirm,
   e.g. "Nice to meet you — did I get that right: Darran, D-A-R-R-A-N?" (spell only the NAME;
   never NATO the email). Yes → filled. No / a correction ("it's Darran with an A",
   "D A R R A N", "no, Darran") → re-capture. If the second try is still not confirmed, ask
   them to spell it letter by letter and assemble the letters (handle "D as in David",
   "double R", spaces/commas/dashes). Cap at 3 attempts then keep the best candidate
   (status filled, flag low_confidence) and move on — never trap the user.
2. TEXT: confirm only when the name is unusual or confidence is low (e.g. extraction
   confidence < 0.8, contains digits/symbols, is > 2 words, looks like a sentence, or differs
   from the typed text's casing in a surprising way). A plain typed "Darran" must NOT trigger
   a confirmation (typed text is authoritative).
3. Corrections at ANY later point ("actually it's spelled Darran", "you've got my name wrong")
   update user_name via the existing change_answer path; the ack says the corrected name.
4. The brain (brain/engine.py + validators + flow.yaml) decides all of this; the LLM only
   extracts. Deterministic templates for the read-back/spelling lines (llm/templates.py
   confirm_line / spell). Voice (voice/flows.py) must pass STT confidence through and speak
   the templated confirmation.
5. Tests: unit (spelling assembler incl. "double r", "as in"), brain (voice always confirms
   user_name; text plain name no confirm; low-confidence text confirms; correction flow;
   3-attempt cap), voice flow test driving "Darren" → "no, it's D A R R A N" → filled Darran.

## WHY
Name correctness is table stakes; a misspelled name on the graduation screen is the
first thing a reviewer notices.

## SCOPE
services/agent/agent/brain/** (confirm policy for user_name only), packages/flow/flow.yaml
(user_name confirm config only), services/agent/agent/llm/templates.py (name confirm/spell
lines), services/agent/agent/llm/extract.py + llm/schema.py ONLY if a `spelled_letters` /
confidence field is needed, services/agent/agent/voice/flows.py (name confirm path only),
services/agent/tests/**.
Do NOT touch: apps/web/**, agent/api/** (GRAD-001 owns), brain/engine.py `apply()` graduated
branch (GRAD-001), harness/**, docs/qa/** (except you may append a short "NAME-001" note to
docs/qa/live-test-2026-09-27.md if it exists).

## READ
CLAUDE.md; docs/ARCHITECTURE.md (brain/phrasing/voice); packages/flow/flow.yaml;
services/agent/agent/brain/{engine,validators,state,spec}.py; llm/{templates,extract,schema}.py;
voice/flows.py; tests/test_voice_*.py; tests/test_brain_*.py.

## DO NOT READ
.env*, .persona-deploy/**, any secrets; never touch penciled-emr.

## REQUIREMENTS
- Offline tests only (no vendor calls in qa tiers). Keep all existing tests green.
- The LLM never decides whether to confirm — code does.
- Keep spoken lines short (< 25 words each).

## ACCEPTANCE
- [ ] `npm run qa:fast`, `npm run qa:flow` green (use PATH=/opt/anaconda3/bin:$PATH)
- [ ] New tests listed in TASK 5 pass
- [ ] no push / no deploy

## OUTPUT
Commit on your branch (no push). Final message: STATUS / FILES / TESTS / COMMIT / RISKS.

## BUDGET
Opus 45 turns.
