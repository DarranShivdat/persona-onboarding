// Server-only helpers for the same-origin proxy (app/api/session/*). The session id +
// token live in an httpOnly cookie; the browser never sees the token or any agent URL.
// Env: PERSONA_AGENT_BASE_URL (agent API origin, e.g. http://127.0.0.1:8000).
import type { AgentState } from "./agent-state";

export const SESSION_COOKIE = "persona_session";
const MAX_AGE_S = 60 * 60 * 24 * 30;

export interface SessionRef {
  id: string;
  token: string;
}

export function agentBaseUrl(): string | null {
  const u = process.env.PERSONA_AGENT_BASE_URL?.trim();
  return u ? u.replace(/\/+$/, "") : null;
}

export function encodeSession(s: SessionRef): string {
  return `${s.id}.${s.token}`;
}

export function decodeSession(raw: string | undefined): SessionRef | null {
  if (!raw) return null;
  const i = raw.indexOf(".");
  if (i <= 0 || i === raw.length - 1) return null;
  const id = raw.slice(0, i);
  const token = raw.slice(i + 1);
  // ids are UUIDs, tokens urlsafe base64: reject anything that could bend the upstream path.
  if (!/^[0-9a-fA-F-]{8,64}$/.test(id) || !/^[A-Za-z0-9_-]{16,128}$/.test(token)) return null;
  return { id, token };
}

export function cookieOptions(secure: boolean) {
  return { httpOnly: true, sameSite: "lax" as const, secure, path: "/", maxAge: MAX_AGE_S };
}

export function isHttps(req: Request): boolean {
  const proto = req.headers.get("x-forwarded-proto") ?? new URL(req.url).protocol.replace(":", "");
  return (proto.split(",")[0] ?? "").trim() === "https";
}

export async function agentFetch(path: string, init: RequestInit & { session?: SessionRef } = {}): Promise<Response> {
  const base = agentBaseUrl();
  if (!base) throw new Error("PERSONA_AGENT_BASE_URL is not set");
  const headers = new Headers(init.headers);
  if (init.session) headers.set("Authorization", `Bearer ${init.session.token}`);
  const { session: _s, ...rest } = init;
  return fetch(`${base}${path}`, { ...rest, headers, cache: "no-store" });
}

export function sessionPath(s: SessionRef, sub = ""): string {
  return `/v1/sessions/${encodeURIComponent(s.id)}${sub}`;
}

/** Snapshot for SSR / resume; null when there is no session or the agent rejects it. */
export async function loadState(s: SessionRef | null): Promise<AgentState | null> {
  if (!s || !agentBaseUrl()) return null;
  try {
    const r = await agentFetch(sessionPath(s), { session: s });
    return r.ok ? ((await r.json()) as AgentState) : null;
  } catch {
    return null;
  }
}

/** Pass an upstream JSON response through (status + body), never leaking upstream headers. */
export async function relay(r: Response): Promise<Response> {
  const body = await r.text();
  return new Response(body || "{}", { status: r.status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
}

export function jsonError(status: number, error: string): Response {
  return Response.json({ error }, { status, headers: { "cache-control": "no-store" } });
}
