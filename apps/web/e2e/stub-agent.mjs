// Test double of the agent API (services/agent/agent/api/README.md) for `qa:e2e`: in-memory,
// no Postgres, no vendor network. Its "brain" is a canned script that mirrors FakeLlm's
// text path (agent_name -> call_offer -> user_name -> need -> gmail -> graduated) closely
// enough to drive the real driver + proxy; it is NOT the brain and never ships.
//
//   node apps/web/e2e/stub-agent.mjs            # PORT (default 3199)
//
// Test-only seeding: `POST /__test/sessions {slots, node, graduated, deferred, transcript}`
// -> `{id, token}` so specs can start mid-flow (EC-08/30/31) and set the session cookie.
import { createServer } from "node:http";
import { randomBytes, randomUUID } from "node:crypto";

const PORT = Number(process.env.PORT || 3199);
const SLOTS = ["agent_name", "user_name", "need", "gmail"];
const YES = new Set(["yes", "sure", "ok", "okay", "call", "yeah", "call me"]);
const sessions = new Map();

function emptySlots() {
  return Object.fromEntries(SLOTS.map((s) => [s, { status: "empty", value: null, source: null, needs_confirm: false }]));
}

function snapshot(s) {
  return {
    id: s.id,
    version: s.version,
    status: s.graduated ? "graduated" : "active",
    node: s.node,
    active_channel: "text",
    slots: s.slots,
    deferred_prompts: s.deferred,
    graduated: s.graduated,
    call: { live: !!s.callId, call_id: s.callId, expires_at: null },
  };
}

function push(s, type, data) {
  s.events.push({ id: ++s.seq, type, data });
  for (const w of s.waiters) w();
}

function fill(s, slot, value) {
  s.slots[slot] = { status: "filled", value, source: "text", needs_confirm: false };
}

function nextMissing(s) {
  return ["user_name", "need", "gmail"].find((k) => s.slots[k].status === "empty") ?? "graduated";
}

const ASK = {
  user_name: "And what should I call you?",
  need: "What's one thing you'd love a hand with this week?",
  gmail: "Last step: connect your Gmail with the button below.",
  graduated: "You're all set — let's get to work.",
};

/** Canned stand-in for extraction + brain + phrasing. */
function turn(s, text) {
  const low = text.trim().toLowerCase();
  let reply;
  if (s.node === "agent_name") {
    fill(s, "agent_name", text.trim());
    s.node = "call_offer";
    reply = `Got it: ${text.trim()}. Want to hop on a quick call for the rest, or keep typing?`;
  } else if (s.node === "call_offer") {
    s.node = YES.has(low) ? "call_offer" : nextMissing(s);
    reply = YES.has(low) ? "Tap the call button when you're ready." : ASK[s.node];
  } else if (s.node === "gmail") {
    s.deferred = ["gmail"];
    s.slots.gmail = { ...s.slots.gmail, status: "skipped" };
    s.node = "graduated";
    s.graduated = true;
    reply = ASK.graduated;
  } else if (s.node === "user_name" || s.node === "need") {
    fill(s, s.node, text.trim());
    s.node = nextMissing(s);
    reply = `Got it: ${text.trim()}. ${ASK[s.node]}`;
  } else {
    reply = "Happy to help with that.";
  }
  s.version += 1;
  push(s, "transcript", { role: "user", text, channel: "text" });
  push(s, "transcript", { role: "assistant", text: reply, channel: "text" });
  push(s, "state", snapshot(s));
  if (s.node === "gmail") push(s, "gmail_connect_card", { node: "gmail" });
  if (s.graduated && low) push(s, "graduate", { deferred: s.deferred });
  return reply;
}

function create(seed = {}) {
  const s = {
    id: randomUUID(),
    token: randomBytes(24).toString("base64url"),
    version: 1,
    node: seed.node ?? "agent_name",
    slots: emptySlots(),
    deferred: seed.deferred ?? [],
    graduated: !!seed.graduated,
    callId: null,
    events: [],
    seq: 0,
    waiters: new Set(),
  };
  for (const [k, v] of Object.entries(seed.slots ?? {})) {
    if (v === "skipped") s.slots[k] = { ...s.slots[k], status: "skipped" };
    else fill(s, k, v);
  }
  sessions.set(s.id, s);
  const hello = "Hi! I'm your new Persona assistant. What would you like to call your assistant?";
  if (!seed.transcript) push(s, "transcript", { role: "assistant", text: hello, channel: "text" });
  for (const [role, text] of seed.transcript ?? []) push(s, "transcript", { role, text, channel: "text" });
  push(s, "state", snapshot(s));
  return { s, reply: hello };
}

function send(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

async function readJson(req) {
  let raw = "";
  for await (const c of req) raw += c;
  try {
    return raw ? JSON.parse(raw) : {};
  } catch {
    return null;
  }
}

function auth(req, url, s) {
  const h = req.headers.authorization?.replace(/^Bearer /, "") ?? req.headers["x-session-token"];
  return (h ?? url.searchParams.get("token")) === s.token;
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const parts = url.pathname.split("/").filter(Boolean);
  if (url.pathname === "/health") return send(res, 200, { ok: true, stub: true });
  if (req.method === "POST" && url.pathname === "/__test/sessions") {
    const { s } = create((await readJson(req)) ?? {});
    return send(res, 201, { id: s.id, token: s.token });
  }
  if (req.method === "POST" && url.pathname === "/v1/sessions") {
    const { s, reply } = create();
    return send(res, 201, { id: s.id, token: s.token, reply, state: snapshot(s), push_ui: [], trace_id: "stub" });
  }
  if (parts[0] !== "v1" || parts[1] !== "sessions" || !parts[2]) return send(res, 404, { error: "not_found" });
  const s = sessions.get(parts[2]);
  if (!s) return send(res, 404, { error: "not_found" });
  if (!auth(req, url, s)) return send(res, 401, { error: "unauthorized" });
  const sub = parts.slice(3).join("/");

  if (req.method === "GET" && sub === "") return send(res, 200, snapshot(s));
  if (req.method === "POST" && sub === "turns") {
    const body = await readJson(req);
    if (!body || typeof body.text !== "string" || !body.text.trim()) return send(res, 422, { error: "bad_body" });
    if (typeof body.version === "number" && body.version !== s.version) return send(res, 409, { error: "version_conflict" });
    const reply = turn(s, body.text);
    return send(res, 200, { reply, state: snapshot(s), push_ui: [], trace_id: "stub" });
  }
  if (req.method === "POST" && sub === "call") {
    if (s.callId) return send(res, 409, { error: "call_in_progress" });
    s.callId = randomUUID();
    return send(res, 201, { call_id: s.callId, lease_expires_at: null, answer: null, status: "lease_acquired" });
  }
  if (req.method === "DELETE" && parts[3] === "call" && parts[4]) {
    const ok = s.callId === parts[4];
    if (ok) s.callId = null;
    return send(res, 200, { released: ok });
  }
  if (req.method === "GET" && sub === "events") {
    const last = req.headers["last-event-id"];
    let after = /^\d+$/.test(last ?? "") ? Number(last) : 0;
    res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
    res.write("retry: 500\n\n");
    const flush = () => {
      for (const e of s.events) {
        if (e.id <= after) continue;
        after = e.id;
        res.write(`id: ${e.id}\nevent: ${e.type}\ndata: ${JSON.stringify(e.data)}\n\n`);
      }
    };
    flush();
    s.waiters.add(flush);
    const ka = setInterval(() => res.write(": keepalive\n\n"), 15000);
    req.on("close", () => {
      clearInterval(ka);
      s.waiters.delete(flush);
    });
    return;
  }
  return send(res, 404, { error: "not_found" });
});

server.listen(PORT, "127.0.0.1", () => console.log(`stub agent on http://127.0.0.1:${PORT}`));
