# Playwright flow tests

`npm run qa:e2e` (or `npm -w apps/web run e2e`). The config builds and serves the app on
:3100, or uses `PERSONA_WEB_URL` if set. Projects: desktop 1440x900, mobile 390x844.

Live specs (FE-002: EC-08, EC-30, EC-31-live) drive the real `ApiSessionDriver` + proxy
against `e2e/stub-agent.mjs`, an in-memory test double of the agent API started by the
Playwright config on :3199 (no Postgres, no vendor network). It has a test-only
`POST /__test/sessions` seeding route used by `e2e/live.ts`. To run the no-seed spec (EC-08)
against the real agent instead (FakeLlm + ephemeral Postgres, e.g. `create_app(store=PgStore(
ephemeral_dsn()), llm=FakeLlm())` under uvicorn): `PERSONA_E2E_AGENT_URL=http://127.0.0.1:<port>
npx -w apps/web playwright test e2e/ec-08-refresh-resume.spec.ts`.

FE-001 specs run against the mock session driver (`?state=<name>`): EC-03 (mic denied),
EC-29 (decline call), EC-31 (graduated return), plus a11y checks from spec §8. One spec per
`e2e`-tier case in `harness/edge-cases.yaml` as FE-002/003 wire the real driver. Voice is
faked with Chromium's `--use-fake-device-for-media-stream --use-file-for-fake-audio-capture`.
