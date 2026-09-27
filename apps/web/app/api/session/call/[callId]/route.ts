// Same-origin proxy: release the call lease.
import type { NextRequest } from "next/server";
import { SESSION_COOKIE, agentFetch, agentBaseUrl, decodeSession, jsonError, relay, sessionPath } from "@/lib/session/server";

export const dynamic = "force-dynamic";

export async function DELETE(req: NextRequest, { params }: { params: Promise<{ callId: string }> }) {
  if (!agentBaseUrl()) return jsonError(503, "agent_unconfigured");
  const s = decodeSession(req.cookies.get(SESSION_COOKIE)?.value);
  if (!s) return jsonError(401, "no_session");
  const { callId } = await params;
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(callId)) return jsonError(422, "bad_call_id");
  const body = await req.text();
  try {
    return await relay(
      await agentFetch(sessionPath(s, `/call/${callId}`), {
        method: "DELETE",
        session: s,
        headers: { "content-type": "application/json" },
        body: body || JSON.stringify({ reason: "user_hangup" }),
      }),
    );
  } catch {
    return jsonError(502, "agent_unreachable");
  }
}
