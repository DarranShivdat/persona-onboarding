// Same-origin proxy: acquire the call lease and exchange SDP. The browser POSTs its offer
// `{sdp, type}`; the agent answers `{call_id, answer: {sdp, type} | null, ...}` or 409
// `call_in_progress` when another tab/device holds the lease (EC-02/EC-09).
import type { NextRequest } from "next/server";
import { SESSION_COOKIE, agentFetch, agentBaseUrl, decodeSession, jsonError, relay, sessionPath } from "@/lib/session/server";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  if (!agentBaseUrl()) return jsonError(503, "agent_unconfigured");
  const s = decodeSession(req.cookies.get(SESSION_COOKIE)?.value);
  if (!s) return jsonError(401, "no_session");
  const body = await req.text();
  try {
    return await relay(
      await agentFetch(sessionPath(s, "/call"), {
        method: "POST",
        session: s,
        headers: { "content-type": "application/json" },
        body: body || "{}",
      }),
    );
  } catch {
    return jsonError(502, "agent_unreachable");
  }
}
