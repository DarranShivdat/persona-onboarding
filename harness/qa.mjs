#!/usr/bin/env node
// QA tier runner. Zero npm dependencies so it runs on a fresh clone.
//   node harness/qa.mjs --tier fast|flow|convo|voice|e2e|audit|visual|live|harness|all
// Writes .persona-qa/last-report.json. Unimplemented tiers report PENDING (exit 0);
// a tier FAILS only if something implemented fails.
import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import os from "node:os";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
const tier = args[args.indexOf("--tier") + 1] || "fast";

function run(cmd, argv, opts = {}) {
  const r = spawnSync(cmd, argv, { cwd: ROOT, encoding: "utf8", env: { ...process.env, ...(opts.env || {}) } });
  return { code: r.status ?? 1, out: (r.stdout || "") + (r.stderr || "") };
}

function resolvePython() {
  if (process.env.PERSONA_PYTHON) return process.env.PERSONA_PYTHON;
  // Prefer an interpreter that already has pytest (Homebrew python3.14 often does not).
  const candidates = [
    "/opt/anaconda3/bin/python3",
    "/Users/darranshivdat/anaconda3/bin/python3",
    "python3.12",
    "python3.11",
    "python3",
  ];
  for (const c of candidates) {
    const r = spawnSync(c, ["-c", "import pytest"], { encoding: "utf8" });
    if ((r.status ?? 1) === 0) return c;
  }
  return "python3";
}
const PY = resolvePython();

function pytest(name, paths, extra = []) {
  const r = run(PY, ["-m", "pytest", "-q", "-rs", ...paths, ...extra]);
  const skipped = Number((r.out.match(/(\d+) skipped/) || [0, 0])[1]);
  const pending = /PENDING/.test(r.out) ? skipped : 0;
  const summary = (r.out.trim().split("\n").filter((l) => /passed|failed|error|skipped|no tests/.test(l)).pop() || "").trim();
  const status = r.code === 0 ? "pass" : r.code === 5 ? "pending" : "fail";
  if (status === "fail") process.stdout.write(r.out);
  return { check: name, status, summary: pending ? `${summary} [${pending} PENDING]` : summary, pending };
}

// qa:convo: one eval run per offline mode (mock, replay); fails on any failed case or network call.
function convo(mode) {
  const r = run(PY, ["-m", "harness.convo", "run", "--mode", mode]);
  const line = r.out.split("\n").find((l) => l.startsWith("CONVO_SUMMARY "));
  const s = line ? JSON.parse(line.slice("CONVO_SUMMARY ".length)) : null;
  const status = r.code === 0 && s && s.ok ? "pass" : "fail";
  if (status === "fail") process.stdout.write(r.out);
  const summary = s
    ? `${s.pass} passed, ${s.fail} failed, ${s.network_calls} network calls, run ${s.run} [${s.pending} PENDING]`
    : "no CONVO_SUMMARY (runner crashed)";
  return { check: `scripted text conversations (${mode} LLM)`, status, summary, pending: s ? s.pending : 0 };
}

const pendingCheck = (name, packet) => ({ check: name, status: "pending", summary: `not implemented yet (${packet})` });

const TIERS = {
  fast: () => [
    pytest("flow-spec + edge-case catalog", ["services/agent/tests/test_spec.py", "harness/tests"]),
  ],
  flow: () => [pytest("flow engine (pure, no LLM)", ["services/agent/tests"])],
  convo: () => [pytest("convo runner + scoring + eval plumbing", ["harness/convo/tests"]), convo("mock"), convo("replay")],
  voice: () => [pendingCheck("headless Pipecat voice client + fault injection", "HARNESS-003")],
  e2e: () => {
    if (!existsSync(join(ROOT, "node_modules/@playwright/test"))) {
      return [{ check: "Playwright flow tests (mock driver)", status: "fail", summary: "deps not installed: run npm install" }];
    }
    const r = run("npm", ["-w", "apps/web", "run", "e2e"]);
    const summary = r.out.split("\n").filter((l) => /\d+ (passed|failed|flaky|skipped)/.test(l)).map((l) => l.trim()).join(", ");
    if (r.code !== 0) process.stdout.write(r.out);
    return [{ check: "Playwright flow tests (mock driver)", status: r.code === 0 ? "pass" : "fail", summary }];
  },
  // AUDIT-001: every control on every screen (desktop + mobile), LOCAL stub target, offline.
  // LIVE: PERSONA_AUDIT_URL=<web url> (scripts/deploy/smoke.sh --audit). PERSONA_AUDIT_DOCS=1
  // also writes docs/qa/button-audit{.md,/}.
  audit: () => {
    if (!existsSync(join(ROOT, "node_modules/@playwright/test"))) {
      return [{ check: "Playwright button audit", status: "fail", summary: "deps not installed: run npm install" }];
    }
    const r = run("npm", ["-w", "apps/web", "run", "audit"]);
    const summary = r.out.split("\n").filter((l) => /\d+ (passed|failed|flaky|skipped)|\[audit\] \d+ rows/.test(l)).map((l) => l.trim()).join(", ");
    if (r.code !== 0) process.stdout.write(r.out);
    const target = process.env.PERSONA_AUDIT_URL ? `LIVE ${process.env.PERSONA_AUDIT_URL}` : "LOCAL stub";
    return [{ check: `Playwright button audit (${target})`, status: r.code === 0 ? "pass" : "fail", summary }];
  },
  visual: () => {
    const passthru = args.filter((a, i) => a !== "--tier" && args[i - 1] !== "--tier");
    const r = run("node", ["harness/visual/capture.mjs", ...passthru]);
    return [{ check: "screenshot capture + diff vs docs/design/mockups", status: /PENDING/.test(r.out) ? "pending" : r.code === 0 ? "pass" : "fail", summary: r.out.trim() }];
  },
  live: () => {
    if (process.env.PERSONA_QA_LIVE !== "1") {
      return [{ check: "live LLM + judge -> eval backend", status: "skipped", summary: "set PERSONA_QA_LIVE=1 (spends real API calls)" }];
    }
    return [pendingCheck("live LLM + judge -> Langfuse dataset run", "OBS-002")];
  },
  harness: () => {
    const r = run("bash", ["scripts/test-persona-harness.sh"]);
    const line = r.out.split("\n").filter((l) => /SUMMARY|HARNESS TESTS/.test(l)).join(" | ");
    if (r.code !== 0) process.stdout.write(r.out);
    return [{ check: "supervisor mock harness (no Claude)", status: r.code === 0 ? "pass" : "fail", summary: line }];
  },
};
TIERS.all = () => ["fast", "flow", "convo", "voice", "e2e", "audit", "visual", "live"].flatMap((t) => TIERS[t]().map((c) => ({ tier: t, ...c })));

if (!TIERS[tier]) {
  console.error(`unknown tier ${tier}; one of ${Object.keys(TIERS).join(", ")}`);
  process.exit(2);
}
const started = Date.now();
const checks = TIERS[tier]().map((c) => ({ tier: c.tier || tier, ...c }));
const failed = checks.filter((c) => c.status === "fail");
const report = {
  tier,
  ok: failed.length === 0,
  execution: { host: os.hostname(), cwd: ROOT, at: new Date().toISOString(), ms: Date.now() - started },
  checks,
};
mkdirSync(join(ROOT, ".persona-qa"), { recursive: true });
writeFileSync(join(ROOT, ".persona-qa/last-report.json"), JSON.stringify(report, null, 2) + "\n");
for (const c of checks) console.log(`[qa:${c.tier}] ${c.status.toUpperCase().padEnd(7)} ${c.check}${c.summary ? " — " + c.summary : ""}`);
const pendingItems = checks.reduce((n, c) => n + (c.status === "pending" ? 1 : 0) + (c.pending || 0), 0);
console.log(`[qa:${tier}] ${report.ok ? "OK" : "FAILED"} (${pendingItems} pending items) -> .persona-qa/last-report.json`);
process.exit(report.ok ? 0 : 1);
