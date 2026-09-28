# GRAD-001 — Make the graduation (home) screen fully functional

CLASS: B-high
MODEL: opus
ROLE: frontend
WORKTREE: ../persona-onboarding-worktrees/grad-001
BRANCH: claude/grad-001
DEPENDS ON: main @ HEAD

## TASK
The brief requires EARLY GRADUATION into the product, so the graduation screen
(apps/web/components/Home.tsx: "You're all set, <name>." + tiles for assistant/need/Gmail +
"Message <Agent>…" composer) is a real product surface. In Darran's live test the composer did
nothing and nothing could be edited. Make every control on it work:
1. MESSAGING: after graduation, a message gets a real, SCOPED reply (not silence, not a
   repeat of "You're all set"). Agent side: in `brain/engine.py apply()` the `st.graduated`
   branch currently returns an empty plan. Add a small post-graduation handler (new module
   `brain/home.py`, called from that branch only) that: (a) applies edits the user asks for in
   chat ("call me Darran", "rename yourself Nova", "I also want help with my calendar") through
   the SAME validators as onboarding (change_answer on agent_name/user_name/need; need edits
   append when the user says "also"), (b) answers questions about what the assistant will do /
   privacy using approved product facts only, (c) offers Connect Gmail if not connected,
   (d) is honest that this trial doesn't execute tasks yet (no fabricated inbox facts, no
   claims of sending anything). Phrasing: text path uses `api/claude_llm.py` (reaction-only +
   templated tail) — add a `home` reply template set; keep FakeLlm/template path working
   offline. Transcript + SSE must deliver the reply to the home screen.
2. EDITABLE FIELDS: name, assistant name and need tiles are tap-to-edit (inline input, Enter
   saves, Esc cancels, validation errors shown inline) via a new web route
   `POST /api/session/edit {slot, value}` → agent `POST /v1/sessions/{id}/edit` → brain
   change_answer (validators decide; code, not LLM). Also editable by chatting (item 1).
   Home re-renders from the new state; the greeting ("You're all set, <name>.") updates.
3. CONNECT GMAIL on the home screen starts the real OAuth flow and returns to home with the
   tile showing connected (reuse the GmailCard/oauth flow; handle cancel/error states).
4. DISMISS: the deferred-prompt X hides it (already view-state) — make sure it is keyboard
   accessible and persists for the visit; if there is any other "dismiss"/close affordance,
   it must work. Nothing on the screen may look clickable without doing something.
5. Show the post-graduation conversation on the home screen (a compact thread under the
   tiles) so replies are visible.
6. Tests: agent unit tests for brain/home.py (edits via validators, invalid edit rejected,
   question answered from facts, no state change on prompt injection); API test for /edit;
   Playwright e2e (stub agent + real-agent variant if PERSONA_E2E_AGENT_URL set): send a
   message on home → reply appears; tap-to-edit user name → greeting updates; dismiss works;
   Connect Gmail navigates to the OAuth start (mock).

## WHY
Early graduation is a brief requirement; a dead composer on the final screen fails it.

## SCOPE
apps/web/components/{Home,App,Composer}.tsx, apps/web/lib/session/**, apps/web/app/api/session/**
(new edit route), apps/web/app/globals.css (home styles only), apps/web/e2e/grad-*.spec.ts,
apps/web/e2e/stub-agent.mjs (home replies + edit endpoint),
services/agent/agent/brain/home.py (new), brain/engine.py (ONLY the `if st.graduated:` branch
in apply()), services/agent/agent/api/** (edit endpoint, home turn wiring, claude_llm home
templates), services/agent/agent/llm/templates.py (new HOME_* constants only),
services/agent/tests/test_home_*.py, services/agent/tests/test_api_edit*.py.
Do NOT touch: voice/**, flow.yaml user_name confirm config (NAME-001), harness/**,
apps/web/e2e/audit/** (AUDIT-001), docs/qa/button-audit.md.

## READ
CLAUDE.md; docs/design/spec.md §4.6 (home); docs/design/review/copy.md; docs/ARCHITECTURE.md;
apps/web/components/*.tsx; apps/web/lib/session/{api-driver,agent-state,types}.ts;
apps/web/app/api/session/**; services/agent/agent/api/{app,service,claude_llm,llm}.py;
services/agent/agent/brain/engine.py; docs/product-facts.md.

## DO NOT READ
.env*, .persona-deploy/**, secrets; never touch penciled-emr.

## REQUIREMENTS
- Code decides every state change; the LLM only extracts + phrases (reaction-only).
- No fabricated capabilities; replies < 40 words.
- Ports if a local stack is needed: agent :8410, web :3410; scratch DB `persona_grad001`.
- Offline tests in qa tiers.

## ACCEPTANCE
- [ ] `npm run qa:fast`, `npm run qa:flow`, `npm run qa:e2e` green (PATH=/opt/anaconda3/bin:$PATH)
- [ ] New grad e2e + agent tests pass
- [ ] no push / no deploy

## OUTPUT
Commit on your branch (no push). Final message: STATUS / FILES / TESTS / COMMIT / RISKS.

## BUDGET
Opus 55 turns.
