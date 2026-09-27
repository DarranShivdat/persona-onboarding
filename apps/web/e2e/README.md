# Playwright flow tests

`npm run qa:e2e` (or `npm -w apps/web run e2e`). The config builds and serves the app on
:3100, or uses `PERSONA_WEB_URL` if set. Projects: desktop 1440x900, mobile 390x844.

FE-001 specs run against the mock session driver (`?state=<name>`): EC-03 (mic denied),
EC-29 (decline call), EC-31 (graduated return), plus a11y checks from spec §8. One spec per
`e2e`-tier case in `harness/edge-cases.yaml` as FE-002/003 wire the real driver. Voice is
faked with Chromium's `--use-fake-device-for-media-stream --use-file-for-fake-audio-capture`.
