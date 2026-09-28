import type { BrowserContext } from "@playwright/test";

// Helpers for live-driver specs (real ApiSessionDriver + /api/session proxy + stub agent).
export const AGENT_URL = process.env.PERSONA_E2E_AGENT_URL ?? "http://127.0.0.1:3199";
export const SESSION_COOKIE = "persona_session";

export interface Seed {
  node: string;
  slots?: Record<string, string>;
  deferred?: string[];
  graduated?: boolean;
  /** A call already live on another device (stub holds the lease). */
  call?: boolean;
  transcript?: [role: "user" | "assistant", text: string][];
}

export interface SessionRef {
  id: string;
  token: string;
}

/** Create a session in the stub agent (test-only seeding route). */
export async function seed(seed: Seed): Promise<SessionRef> {
  const r = await fetch(`${AGENT_URL}/__test/sessions`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(seed) });
  if (!r.ok) throw new Error(`seeding needs the stub agent (e2e/stub-agent.mjs): ${r.status}`);
  return (await r.json()) as SessionRef;
}

/** Hand a session's cookie to a browser context (a prior visit, or a second device: EC-09). */
export async function useSession(context: BrowserContext, baseURL: string, s: SessionRef): Promise<void> {
  await context.addCookies([{ name: SESSION_COOKIE, value: `${s.id}.${s.token}`, url: baseURL, httpOnly: true, sameSite: "Lax" }]);
}

/** Seed a session in the stub agent and hand its cookie to the browser (a prior visit). */
export async function seedSession(context: BrowserContext, baseURL: string, s: Seed): Promise<string> {
  const ref = await seed(s);
  await useSession(context, baseURL, ref);
  return ref.id;
}

/** A session parked at the Gmail step (name, user and need already filled). */
export const AT_GMAIL: Seed = {
  node: "gmail",
  slots: { agent_name: "Juno", user_name: "Maya", need: "Inbox triage" },
  transcript: [["assistant", "Last step: connect your Gmail with the button below."]],
};
