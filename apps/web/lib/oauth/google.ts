// Server-only Google OAuth (testing mode) for the Gmail connect card (ARCHITECTURE §6, §11).
// The browser opens /api/oauth/google/start in a popup; the callback verifies the `openid`
// identity and posts the verified address server-to-server to the agent's gmail route. The
// brain decides what that means; nothing here moves the flow.
//
// Env (names only, see .env.example): GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET,
// GOOGLE_OAUTH_REDIRECT_URL (optional; defaults to <origin>/api/oauth/google/callback),
// PERSONA_INTERNAL_SECRET (agent gmail route; AGENT_API_SHARED_SECRET accepted as an alias).
// Test-only overrides for the e2e mock double: GOOGLE_OAUTH_AUTH_URL, GOOGLE_OAUTH_TOKEN_URL,
// GOOGLE_OAUTH_ISSUER.
import { createHash, randomBytes } from "node:crypto";

export const OAUTH_COOKIE = "persona_oauth";
const FLOW_TTL_S = 10 * 60;

export const GMAIL_SCOPES = {
  read: "https://www.googleapis.com/auth/gmail.readonly",
  organize: "https://www.googleapis.com/auth/gmail.modify",
  send: "https://www.googleapis.com/auth/gmail.send",
} as const;
export const SCOPES = ["openid", "email", "profile", GMAIL_SCOPES.read, GMAIL_SCOPES.organize, GMAIL_SCOPES.send];

export type Capability = keyof typeof GMAIL_SCOPES;

/** What the popup reports back to the card. Rendering data only. */
export type OAuthResult =
  | { status: "connected" | "wrong_account"; email: string; name: string; missing: Capability[] }
  | { status: "error"; reason: string };

export interface GoogleConfig {
  clientId: string;
  clientSecret: string;
  authUrl: string;
  tokenUrl: string;
  issuers: string[];
}

export function googleConfig(): GoogleConfig | null {
  const clientId = process.env.GOOGLE_OAUTH_CLIENT_ID?.trim();
  const clientSecret = process.env.GOOGLE_OAUTH_CLIENT_SECRET?.trim();
  if (!clientId || !clientSecret) return null;
  const issuer = process.env.GOOGLE_OAUTH_ISSUER?.trim();
  return {
    clientId,
    clientSecret,
    authUrl: process.env.GOOGLE_OAUTH_AUTH_URL?.trim() || "https://accounts.google.com/o/oauth2/v2/auth",
    tokenUrl: process.env.GOOGLE_OAUTH_TOKEN_URL?.trim() || "https://oauth2.googleapis.com/token",
    issuers: issuer ? [issuer] : ["https://accounts.google.com", "accounts.google.com"],
  };
}

export function internalSecret(): string | null {
  return (process.env.PERSONA_INTERNAL_SECRET ?? process.env.AGENT_API_SHARED_SECRET)?.trim() || null;
}

export function redirectUri(req: Request): string {
  const fixed = process.env.GOOGLE_OAUTH_REDIRECT_URL?.trim();
  if (fixed) return fixed;
  const u = new URL(req.url);
  const proto = (req.headers.get("x-forwarded-proto") ?? u.protocol.replace(":", "")).split(",")[0]!.trim();
  const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host") ?? u.host;
  return `${proto}://${host}/api/oauth/google/callback`;
}

const b64url = (b: Buffer) => b.toString("base64url");

/** Per-attempt state kept in an httpOnly cookie: CSRF `state`, PKCE verifier, OIDC nonce, session binding. */
export interface FlowState {
  state: string;
  verifier: string;
  nonce: string;
  sid: string;
  exp: number;
}

export function newFlow(sid: string): FlowState {
  return { state: b64url(randomBytes(24)), verifier: b64url(randomBytes(48)), nonce: b64url(randomBytes(24)), sid, exp: Math.floor(Date.now() / 1000) + FLOW_TTL_S };
}

export function encodeFlow(f: FlowState): string {
  return b64url(Buffer.from(JSON.stringify(f)));
}

export function decodeFlow(raw: string | undefined): FlowState | null {
  if (!raw) return null;
  try {
    const f = JSON.parse(Buffer.from(raw, "base64url").toString("utf8")) as FlowState;
    if (typeof f.state !== "string" || typeof f.verifier !== "string" || typeof f.nonce !== "string" || typeof f.sid !== "string") return null;
    return f.exp > Date.now() / 1000 ? f : null;
  } catch {
    return null;
  }
}

export function flowCookieOptions(secure: boolean) {
  // Lax: the cookie must ride along on Google's top-level redirect back to the callback.
  return { httpOnly: true, sameSite: "lax" as const, secure, path: "/api/oauth/google", maxAge: FLOW_TTL_S };
}

export function authorizeUrl(cfg: GoogleConfig, f: FlowState, redirect: string, switchAccount: boolean): string {
  const u = new URL(cfg.authUrl);
  u.search = new URLSearchParams({
    client_id: cfg.clientId,
    redirect_uri: redirect,
    response_type: "code",
    scope: SCOPES.join(" "),
    access_type: "offline",
    // consent: always get a refresh token; select_account: "Use a different account" (EC-22).
    prompt: switchAccount ? "consent select_account" : "consent",
    include_granted_scopes: "true",
    state: f.state,
    nonce: f.nonce,
    code_challenge: b64url(createHash("sha256").update(f.verifier).digest()),
    code_challenge_method: "S256",
  }).toString();
  return u.toString();
}

export interface TokenResponse {
  id_token?: string;
  access_token?: string;
  refresh_token?: string;
  scope?: string;
}

export async function exchangeCode(cfg: GoogleConfig, code: string, verifier: string, redirect: string): Promise<TokenResponse | null> {
  try {
    const r = await fetch(cfg.tokenUrl, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "authorization_code", code, code_verifier: verifier, client_id: cfg.clientId, client_secret: cfg.clientSecret, redirect_uri: redirect }).toString(),
      cache: "no-store",
    });
    return r.ok ? ((await r.json()) as TokenResponse) : null;
  } catch {
    return null;
  }
}

export interface Identity {
  sub: string;
  email: string;
  name: string;
  hd: string | null;
}

/**
 * Claims check for an ID token received directly from the token endpoint over TLS with our
 * client secret (OIDC Core §3.1.3.7: TLS validation may stand in for the signature check).
 * Still enforces iss, aud, exp, nonce and a verified email.
 */
export function verifyIdToken(cfg: GoogleConfig, idToken: string | undefined, nonce: string): Identity | null {
  const part = idToken?.split(".")[1];
  if (!part) return null;
  let c: Record<string, unknown>;
  try {
    c = JSON.parse(Buffer.from(part, "base64url").toString("utf8")) as Record<string, unknown>;
  } catch {
    return null;
  }
  const aud = Array.isArray(c.aud) ? c.aud : [c.aud];
  const now = Date.now() / 1000;
  if (!cfg.issuers.includes(String(c.iss)) || !aud.includes(cfg.clientId)) return null;
  if (typeof c.exp !== "number" || c.exp < now - 60 || c.nonce !== nonce) return null;
  if (typeof c.sub !== "string" || !c.sub || typeof c.email !== "string" || !c.email) return null;
  if (c.email_verified !== true && c.email_verified !== "true") return null;
  const email = c.email.toLowerCase();
  return { sub: c.sub, email, name: typeof c.name === "string" && c.name ? c.name : email, hd: typeof c.hd === "string" ? c.hd : null };
}

/** Gmail capabilities Google did not grant (the user can untick scopes on the consent screen). */
export function missingCapabilities(granted: string[]): Capability[] {
  const has = (s: string) => granted.includes(s);
  const out: Capability[] = [];
  if (!has(GMAIL_SCOPES.read) && !has(GMAIL_SCOPES.organize)) out.push("read"); // modify implies read
  if (!has(GMAIL_SCOPES.organize)) out.push("organize");
  if (!has(GMAIL_SCOPES.send)) out.push("send");
  return out;
}

/** The popup page: reports the result to the opener (postMessage + BroadcastChannel, which survives a COOP-severed opener) and closes. */
export function resultPage(result: OAuthResult): Response {
  const json = JSON.stringify({ type: "persona:gmail", ...result }).replace(/</g, "\\u003c");
  const title = result.status === "error" ? "Gmail isn’t connected yet" : "Gmail connected";
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title>
<style>body{font:17px/1.45 system-ui,sans-serif;margin:0;display:grid;place-items:center;min-height:100vh;color:#0b0b0c}main{max-width:360px;padding:24px;text-align:center}a{color:inherit}</style></head>
<body><main><h1 style="font-size:20px">${title}</h1><p>You can close this window and go back to Persona.</p><p><a href="/">Back to Persona</a></p></main>
<script>(function(){var r=${json};try{var c=new BroadcastChannel("persona-gmail");c.postMessage(r);c.close()}catch(e){}try{if(window.opener)window.opener.postMessage(r,location.origin)}catch(e){}setTimeout(function(){window.close()},150)})();</script></body></html>`;
  return new Response(html, { status: 200, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", "referrer-policy": "no-referrer" } });
}
