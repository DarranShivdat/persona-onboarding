// Same-origin proxy: Google consent didn't finish (EC-21). The agent never changes state for
// it; a live call on the Gmail step offers retry / type-it / skip (VOICE-004-lite).
import type { NextRequest } from "next/server";
import { SESSION_COOKIE, agentFetch, agentBaseUrl, decodeSession, jsonError, relay, sessionPath } from "@/lib/session/server";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  if (!agentBaseUrl()) return jsonError(503, "agent_unconfigured");
  const s = decodeSession(req.cookies.get(SESSION_COOKIE)?.value);
  if (!s) return jsonError(401, "no_session");
  const body = (await req.json().catch(() => null)) as { reason?: unknown } | null;
  const reason = typeof body?.reason === "string" && /^[a-z_]{1,32}$/.test(body.reason) ? body.reason : "cancelled";
  try {
    return await relay(
      await agentFetch(sessionPath(s, "/gmail/failed"), {
        method: "POST",
        session: s,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ reason }),
      }),
    );
  } catch {
    return jsonError(502, "agent_unreachable");
  }
}
