import type { BrowserContext } from "@playwright/test";

// Helpers for live-driver specs (real ApiSessionDriver + /api/session proxy + stub agent).
export const AGENT_URL = process.env.PERSONA_E2E_AGENT_URL ?? "http://127.0.0.1:3199";
export const SESSION_COOKIE = "persona_session";

export interface Seed {
  node: string;
  slots?: Record<string, string>;
  deferred?: string[];
  graduated?: boolean;
  transcript?: [role: "user" | "assistant", text: string][];
}

/** Seed a session in the stub agent and hand its cookie to the browser (a prior visit). */
export async function seedSession(context: BrowserContext, baseURL: string, seed: Seed): Promise<string> {
  const r = await fetch(`${AGENT_URL}/__test/sessions`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(seed) });
  if (!r.ok) throw new Error(`seeding needs the stub agent (e2e/stub-agent.mjs): ${r.status}`);
  const { id, token } = (await r.json()) as { id: string; token: string };
  await context.addCookies([{ name: SESSION_COOKIE, value: `${id}.${token}`, url: baseURL, httpOnly: true, sameSite: "Lax" }]);
  return id;
}
