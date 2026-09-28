// Same-origin proxy: ICE servers for the browser leg of a call (VOICE-005). The agent mints
// short-lived TURN credentials per request (Cloudflare) or returns its static list, the same
// source its own leg uses (ADR 0001). Session-authenticated and rate-limited by the agent;
// never cached, never logged.
import type { NextRequest } from "next/server";
import { SESSION_COOKIE, agentFetch, agentBaseUrl, decodeSession, jsonError, relay, sessionPath } from "@/lib/session/server";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  if (!agentBaseUrl()) return jsonError(503, "agent_unconfigured");
  const s = decodeSession(req.cookies.get(SESSION_COOKIE)?.value);
  if (!s) return jsonError(401, "no_session");
  try {
    return await relay(await agentFetch(sessionPath(s, "/ice"), { session: s }));
  } catch {
    return jsonError(502, "agent_unreachable");
  }
}
