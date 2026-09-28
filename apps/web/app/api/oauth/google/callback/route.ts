// OAuth callback: verify state + the `openid` identity, then hand the verified address to
// the agent (server-to-server, shared secret). The brain fills `gmail`; the popup page only
// reports what happened so the card can render it.
//
// Tokens: the refresh token is forwarded server-to-server to the agent, which encrypts it
// (AES-GCM, GMAIL-001) before storing. Never logged, never sent to the browser.
import type { NextRequest } from "next/server";
import {
  OAUTH_COOKIE,
  type OAuthResult,
  decodeFlow,
  exchangeCode,
  googleConfig,
  internalSecret,
  missingCapabilities,
  redirectUri,
  resultPage,
  verifyIdToken,
} from "@/lib/oauth/google";
import { SESSION_COOKIE, agentFetch, agentBaseUrl, decodeSession, isHttps, loadState, sessionPath } from "@/lib/session/server";

export const dynamic = "force-dynamic";

async function handle(req: NextRequest): Promise<OAuthResult> {
  const q = req.nextUrl.searchParams;
  const cfg = googleConfig();
  if (!cfg) return { status: "error", reason: "oauth_unconfigured" };
  const flow = decodeFlow(req.cookies.get(OAUTH_COOKIE)?.value);
  const s = decodeSession(req.cookies.get(SESSION_COOKIE)?.value);
  if (!flow || !q.get("state") || q.get("state") !== flow.state) return { status: "error", reason: "state_mismatch" };
  if (!s || s.id !== flow.sid) return { status: "error", reason: "no_session" };
  // EC-21: consent cancelled / denied on Google's screen.
  if (q.get("error")) return { status: "error", reason: q.get("error") === "access_denied" ? "access_denied" : "google_error" };
  const code = q.get("code");
  if (!code) return { status: "error", reason: "no_code" };

  const tok = await exchangeCode(cfg, code, flow.verifier, redirectUri(req));
  if (!tok) return { status: "error", reason: "exchange_failed" };
  const who = verifyIdToken(cfg, tok.id_token, flow.nonce);
  if (!who) return { status: "error", reason: "identity_unverified" };
  const granted = (tok.scope ?? "").split(/\s+/).filter(Boolean);
  const missing = missingCapabilities(granted);
  // Every Gmail box unticked: nothing to connect. Partial grants connect with reduced capability.
  if (missing.length === 3) return { status: "error", reason: "no_gmail_access" };

  const secret = internalSecret();
  if (!secret || !agentBaseUrl()) return { status: "error", reason: "agent_unconfigured" };
  // EC-22 hint (rendering only): a Workspace account, or not the address the user said earlier.
  const before = await loadState(s);
  const said = before?.slots.gmail?.status === "candidate" ? before.slots.gmail.value?.toLowerCase() : null;
  const wrong = !!who.hd || (!!said && said !== who.email);
  try {
    const r = await agentFetch(sessionPath(s, "/gmail"), {
      method: "POST",
      headers: { "content-type": "application/json", "x-persona-internal-secret": secret },
      body: JSON.stringify({ email: who.email, google_sub: who.sub, scopes: granted, refresh_token: tok.refresh_token ?? null }),
    });
    if (!r.ok) return { status: "error", reason: "agent_error" };
  } catch {
    return { status: "error", reason: "agent_unreachable" };
  }
  return { status: wrong ? "wrong_account" : "connected", email: who.email, name: who.name, missing };
}

export async function GET(req: NextRequest) {
  const res = resultPage(await handle(req));
  // One attempt per flow cookie: clear it whatever happened.
  res.headers.append("set-cookie", `${OAUTH_COOKIE}=; Path=/api/oauth/google; Max-Age=0; HttpOnly; SameSite=Lax${isHttps(req) ? "; Secure" : ""}`);
  return res;
}
