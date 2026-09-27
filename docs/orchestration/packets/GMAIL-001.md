# GMAIL-001 — Server Gmail client + encrypted refresh-token store

CLASS: B-high
MODEL: opus
ROLE: impl
WORKTREE: ../persona-onboarding-worktrees/gmail-001
BRANCH: claude/gmail-001
DEPENDS ON: FLOW-003 (merged), FE-003 (merged)

## TASK
Implement the server-side Gmail client and encrypted refresh-token persistence behind
the existing `POST /v1/sessions/{id}/gmail` contract. Store verified Google identity +
granted scopes + encrypted refresh token; refresh on use; map `invalid_grant` → reconnect
signal; support revoke/disconnect. Add a read-only value-demo helper that pulls safe
metadata from recorded fixtures (subject/from/snippet only). Draft/send/modify paths must
require an explicit confirm turn (brain/UI later) — unit-test that no-send-without-confirm.

## WHY
FE-003 posts verified email after OAuth; reviewers need real (or fixture-backed) Gmail
value demo and safe token handling before VOICE-004 / product test. Half the demo is
automation — write scopes are in scope but gated.

## SCOPE
services/agent/agent/gmail/** (new), services/agent/agent/store/** (gmail_connections
encrypt/refresh fields as needed), services/agent/agent/api/** (wire store + reconnect
errors; keep FE contract), services/agent/tests/test_gmail_*.py (new; recorded fixtures
under services/agent/tests/fixtures/gmail/). Optional narrow brain hook only if confirm
gate needs a shared helper. Do not edit apps/**.

## READ
- CLAUDE.md; docs/ARCHITECTURE.md §2, §5–§6, §11; docs/product-facts.md (Gmail section)
- services/agent/agent/api/app.py (GmailIn, gmail route); agent/api/service.py
- services/agent/agent/store/postgres.py (gmail_connections); agent/brain/validators.py (gmail_oauth)
- .env.example (GOOGLE_OAUTH_*, PERSONA_INTERNAL_SECRET, any TOKEN_ENCRYPT key name you add)

## DO NOT READ
- .env*, secrets; Penciled sensitive paths (.env*, transcripts/, data/, demo patients, PHI)
  + other Penciled repos; never modify penciled-emr; apps/**; docs/design/**

## REQUIREMENTS
- Encrypt refresh tokens at rest (Fernet or libsodium; key from env e.g.
  `PERSONA_TOKEN_ENCRYPT_KEY`). Never log token values. Document key generation in OUTPUT.
- On gmail POST: upsert connection (google_sub, email, scopes, encrypted refresh if
  provided). FE-003 may still stub the refresh token — accept missing refresh with a
  clear `token_status` and do not invent tokens.
- Refresh helper: given stored connection, refresh access token; on `invalid_grant` /
  revoked mark connection and return a structured reconnect error the API can surface.
- Revoke/disconnect endpoint or store method: clear tokens + set revoked_at; idempotent.
- Value demo (read-only): function that returns 1–3 recent message summaries from
  **recorded HTTP fixtures** (no live Google in default pytest). Live call optional/PENDING
  when keys exist — document how.
- Write actions (draft/send/modify): implement client methods but refuse unless
  `confirm_token` / explicit confirm flag matching a prior turn is present; unit test
  proves send is blocked without confirm.
- Env names only in .env.example — never commit values. No Google Cloud resource creation.

## ACCEPTANCE
- [ ] `npm run qa:fast`
- [ ] `pytest services/agent/tests/test_gmail_*.py` (+ existing agent tests still green)
- [ ] Recorded-fixture tests cover: store encrypt/decrypt roundtrip, refresh success,
      invalid_grant → reconnect, revoke, value-demo metadata shape, no-send-without-confirm
- [ ] Default pytest makes zero Google network calls

## CONSTRAINTS
No LangChain. No push. No deploy. No browser OAuth UI (FE-003 done). Voice Gmail push
card is VOICE-004. Do not weaken PERSONA_INTERNAL_SECRET check on gmail route.

## OUTPUT
Commit (no push). STATUS / FILES / TESTS / COMMIT / RISKS.

## BUDGET
Opus 40 turns.
