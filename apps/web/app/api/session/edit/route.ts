// Same-origin proxy: home tap-to-edit (GRAD-001). The agent's validators decide; a 422 carries
// the reason + message the field shows inline. The browser never decides whether a value is valid.
import type { NextRequest } from "next/server";
import { SESSION_COOKIE, agentFetch, agentBaseUrl, decodeSession, jsonError, relay, sessionPath } from "@/lib/session/server";

export const dynamic = "force-dynamic";
const SLOTS = new Set(["agent_name", "user_name", "need"]);
const MAX_VALUE = 2000;

export async function POST(req: NextRequest) {
  if (!agentBaseUrl()) return jsonError(503, "agent_unconfigured");
  const s = decodeSession(req.cookies.get(SESSION_COOKIE)?.value);
  if (!s) return jsonError(401, "no_session");
  const body = (await req.json().catch(() => null)) as { slot?: unknown; value?: unknown } | null;
  const slot = typeof body?.slot === "string" ? body.slot : "";
  const value = typeof body?.value === "string" ? body.value.trim() : "";
  if (!SLOTS.has(slot)) return jsonError(422, "bad_slot");
  if (!value || value.length > MAX_VALUE) return jsonError(422, "bad_value");
  try {
    return await relay(
      await agentFetch(sessionPath(s, "/edit"), {
        method: "POST",
        session: s,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ slot, value }),
      }),
    );
  } catch {
    return jsonError(502, "agent_unreachable");
  }
}
