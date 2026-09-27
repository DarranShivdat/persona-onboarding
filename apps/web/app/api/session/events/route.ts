// Same-origin SSE proxy: `/v1/sessions/{id}/events` with the token added server-side, so
// the browser's EventSource needs no credentials in the URL. `Last-Event-ID` (native
// EventSource reconnect) or `?after=` (driver reconnect) is forwarded upstream.
import type { NextRequest } from "next/server";
import { SESSION_COOKIE, agentFetch, agentBaseUrl, decodeSession, jsonError, sessionPath } from "@/lib/session/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest) {
  if (!agentBaseUrl()) return jsonError(503, "agent_unconfigured");
  const s = decodeSession(req.cookies.get(SESSION_COOKIE)?.value);
  if (!s) return jsonError(401, "no_session");
  const last = req.headers.get("last-event-id") ?? req.nextUrl.searchParams.get("after") ?? "";
  const headers: Record<string, string> = { accept: "text/event-stream" };
  if (/^\d+$/.test(last)) headers["Last-Event-ID"] = last;
  let up: Response;
  try {
    up = await agentFetch(sessionPath(s, "/events"), { session: s, headers, signal: req.signal });
  } catch {
    return jsonError(502, "agent_unreachable");
  }
  if (!up.ok || !up.body) return jsonError(up.status === 404 || up.status === 401 ? up.status : 502, "stream_unavailable");
  return new Response(up.body, {
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  });
}
