# Playwright flow tests (FE-001+)

One spec per `e2e`-tier case in `harness/edge-cases.yaml` (EC-03, EC-08, EC-09,
EC-20, EC-21, EC-22, EC-29, EC-30, EC-31, ...). Voice is faked in e2e with
Chromium's `--use-fake-device-for-media-stream --use-file-for-fake-audio-capture`.
