// Same-origin proxy: one text turn. The brain replies over SSE as well; the JSON reply is
// only used by the driver for errors / version conflicts.
import type { NextRequest } from "next/server";
import { SESSION_COOKIE, agentFetch, agentBaseUrl, decodeSession, jsonError, relay, sessionPath } from "@/lib/session/server";

export const dynamic = "force-dynamic";
const MAX_TEXT = 2000;

export async function POST(req: NextRequest) {
  if (!agentBaseUrl()) return jsonError(503, "agent_unconfigured");
  const s = decodeSession(req.cookies.get(SESSION_COOKIE)?.value);
  if (!s) return jsonError(401, "no_session");
  const body = (await req.json().catch(() => null)) as { text?: unknown; version?: unknown } | null;
  const text = typeof body?.text === "string" ? body.text.trim() : "";
  if (!text || text.length > MAX_TEXT) return jsonError(422, "bad_text");
  const payload: { text: string; version?: number } = { text };
  if (typeof body?.version === "number") payload.version = body.version;
  try {
    return await relay(
      await agentFetch(sessionPath(s, "/turns"), {
        method: "POST",
        session: s,
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      }),
    );
  } catch {
    return jsonError(502, "agent_unreachable");
  }
}
