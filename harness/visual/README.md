# harness/visual — screenshot capture + diff (qa:visual; design-review gate)

```
npm -w apps/web run build && npm -w apps/web run start      # serves :3000 (mock driver)
npm run qa:visual -- --url http://localhost:3000
```

- `capture.mjs --url <base> [--out <dir>] [--states a,b] [--no-diff]`: Playwright opens
  `/?state=<name>&capture=1` for every state in `states.mjs` (11 gated + 5 extra) at
  1440x900 and 390x844 (DPR 1, reduced motion), records `data-mask` rects (call timer,
  captions, ring/waveform) and saves PNGs to `.persona-qa/visual/actual/`, then runs the diff.
- `diff.mjs [--actual <dir>] [--expected docs/design/mockups]`: pixelmatch (threshold 0.1)
  per state with the masks blanked in both images. Writes `.persona-qa/visual/report.json`
  (per-state mismatch fraction, build-sha of the captured app), `diff/<state>@<vp>.png` and
  `contact-sheet@desktop.png` / `contact-sheet@mobile.png` (expected | actual | diff).
- Gate: every gated state ≤ 3% mismatch after masks (FE-001 VISUAL_ACCEPTANCE). Extras are
  scored but not gated.
