# harness/visual — screenshot capture + diff (qa:visual; design-review gate)

- `capture.mjs --url <base> --out <dir>` : Playwright drives the key states
  (landing, chat mid-flow, call offer, phone simulator in-call, Gmail connect card,
  graduation) at 1440x900 and 390x844 and saves PNGs.
- `diff.mjs --actual <dir> --expected docs/design/mockups` : pixelmatch per state,
  writes `.persona-qa/visual/report.json` + side-by-side contact sheet.

Used by: DESIGN worker (reference capture of Persona's product into
docs/design/references/), FRONTEND worker (acceptance), and the EM's design-review
gate before any READY (see AGENTS.md). Implemented in FE-001 (deps: playwright,
pixelmatch, pngjs).
