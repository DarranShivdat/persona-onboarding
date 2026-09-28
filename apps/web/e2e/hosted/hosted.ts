// HOSTED-001 helpers: the live deploy under test. Specs in e2e/hosted/ only run in the
// "hosted" Playwright project (PERSONA_E2E_HOSTED_WEB_URL set; scripts/hosted-e2e.sh).
// Set-but-empty fails closed; unset skips.
function envUrl(name: string): string | undefined {
  const v = process.env[name];
  if (v === undefined) return undefined;
  if (!v.trim()) throw new Error(`${name} is set but empty`);
  return v.trim().replace(/\/+$/, "");
}

export const HOSTED_WEB = envUrl("PERSONA_E2E_HOSTED_WEB_URL");
export const HOSTED_AGENT = envUrl("PERSONA_E2E_HOSTED_AGENT_URL");
export const SKIP_REASON = "set PERSONA_E2E_HOSTED_WEB_URL (bash scripts/hosted-e2e.sh)";
