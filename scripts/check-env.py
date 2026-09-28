#!/usr/bin/env python3
"""Env contract for Persona onboarding: the single registry of every env var the code reads.

  python3 scripts/check-env.py --target agent|web|local [--env-file PATH] [--allow-missing NAME ...]
      Reports each variable for the target by NAME only (present / MISSING / unset / FORBIDDEN /
      INVALID). Never prints values. Exit 1 if a required var is missing/invalid or a
      forbidden (test-only) var is set for a deploy target.
  python3 scripts/check-env.py --markdown             -> docs/deploy/ENV.md body
  python3 scripts/check-env.py --example root|web|agent -> the matching .env.example
  python3 scripts/check-env.py --write                -> regenerate ENV.md + the 3 .env.example files
  python3 scripts/check-env.py --emit agent|web --env-file PATH
      NAME=VALUE for the target's non-empty, non-forbidden vars, for piping straight into
      `fly secrets import` / `vercel env add` by the deploy scripts. The ONLY mode that
      outputs values: never run it to a terminal or a log.

harness/tests/test_check_env.py keeps the generated files in sync and fails if code in
services/agent/agent or apps/web (not e2e) reads a variable missing from REGISTRY.
Stdlib only (runs under qa:fast).
"""
from __future__ import annotations

import argparse
import base64
import binascii
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]

# Where a var is consumed. "agent" = services/agent on Fly, "web" = apps/web on Vercel,
# "local" = scripts/local-stack.sh, "test" = QA/harness only, "platform" = set by the host.
AGENT, WEB, LOCAL, TEST, PLATFORM = "agent", "web", "local", "test", "platform"

GEN_HEX32 = "`openssl rand -hex 32`"
GEN_B64_32 = "`python3 -c \"import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())\"`"


def _valid_token_key(v: str) -> bool:
    try:
        return len(base64.urlsafe_b64decode(v + "=" * (-len(v) % 4))) == 32
    except (binascii.Error, ValueError):
        return False


def _valid_url(v: str) -> bool:
    return v.startswith(("http://", "https://"))


def _valid_pg(v: str) -> bool:
    return v.startswith(("postgres://", "postgresql://"))


@dataclass(frozen=True)
class Var:
    name: str
    components: tuple[str, ...]           # where it is read / must be set
    required: tuple[str, ...] = ()        # check-env targets for which it is required
    secret: bool = False
    default: str = ""                     # code default ("" = none)
    how: str = ""                         # how to obtain / generate
    note: str = ""
    forbidden: tuple[str, ...] = ()       # deploy targets where it must NOT be set (test doubles)
    validate: Optional[Callable[[str], bool]] = field(default=None, compare=False)
    section: str = "core"


REGISTRY: tuple[Var, ...] = (
    # --- core wiring -------------------------------------------------------------------
    Var("PERSONA_DATABASE_URL", (AGENT, LOCAL), required=(AGENT, LOCAL), secret=True,
        how="Supabase → Project Settings → Database → connection string (session pooler :5432 or direct; `?sslmode=require`)",
        note="agent is the single writer; also the argument to scripts/db-migrate.sh", validate=_valid_pg),
    Var("PERSONA_AGENT_BASE_URL", (WEB, LOCAL), required=(WEB, LOCAL),
        how="`https://<fly-app>.fly.dev` (no trailing slash)", note="server-side only; browser never sees it",
        validate=_valid_url),
    Var("PERSONA_INTERNAL_SECRET", (AGENT, WEB, LOCAL), required=(AGENT, WEB, LOCAL), secret=True, how=GEN_HEX32,
        note="SAME value on agent and web: web OAuth callback → agent `POST /v1/sessions/{id}/gmail`"),
    Var("AGENT_API_SHARED_SECRET", (WEB,), secret=True,
        note="legacy alias read by web only if PERSONA_INTERNAL_SECRET is unset — do not set"),
    Var("PERSONA_TOKEN_ENCRYPT_KEY", (AGENT, LOCAL), required=(AGENT, LOCAL), secret=True, how=GEN_B64_32,
        note="AES-GCM key for Google refresh tokens at rest (agent/gmail/crypto.py). Rotating it strands stored tokens",
        validate=_valid_token_key),
    # --- LLM ---------------------------------------------------------------------------
    Var("ANTHROPIC_API_KEY", (AGENT,), required=(AGENT,), secret=True, section="llm",
        how="console.anthropic.com → API keys (set a workspace spend limit)"),
    Var("PERSONA_EXTRACT_MODEL", (AGENT,), default="claude-haiku-4-5", section="llm", note="slot extraction model"),
    Var("PERSONA_PHRASE_MODEL", (AGENT,), default="claude-haiku-4-5", section="llm", note="reply phrasing model"),
    Var("PERSONA_VOICE_LLM_MODEL", (AGENT,), default="claude-haiku-4-5", section="llm", note="voice pipeline LLM"),
    # --- voice ---------------------------------------------------------------------------
    Var("DEEPGRAM_API_KEY", (AGENT,), required=(AGENT,), secret=True, section="voice",
        how="console.deepgram.com → API keys", note="STT (+ Deepgram TTS failover); without it voice mode is `unavailable`"),
    Var("DEEPGRAM_STT_MODEL", (AGENT,), default="nova-3", section="voice"),
    Var("DEEPGRAM_TTS_VOICE", (AGENT,), default="aura-2-thalia-en", section="voice", note="failover TTS voice"),
    Var("CARTESIA_API_KEY", (AGENT,), required=(AGENT,), secret=True, section="voice",
        how="play.cartesia.ai → API keys", note="primary TTS"),
    Var("CARTESIA_VOICE_ID", (AGENT,), default="71a7ad14-091c-4e8e-a314-022ece01c121", section="voice"),
    Var("CARTESIA_MODEL", (AGENT,), default="sonic-2", section="voice"),
    Var("PERSONA_VOICE_MAX_CALL_SECS", (AGENT,), default="900", section="voice", note="hard cap per call (spend guard)"),
    Var("PERSONA_VOICE_DIRECT_SPEECH", (AGENT,), default="1", section="voice", note="speak the brain's line via TTS, skip the phrasing LLM run (LAT-001)"),
    Var("PERSONA_VOICE_FORCE_EXTRACTION", (AGENT,), default="1", section="voice", note="force the record_slots tool call + lean schema on voice (LAT-003; 0 = kill switch)"),
    Var("PERSONA_VOICE_QUICK_ACK", (AGENT,), default="0", section="voice", note="speak a short ack while extraction runs (LAT-003; behaviour change, keep OFF in prod until approved)"),
    Var("PERSONA_VOICE_QUICK_ACK_MS", (AGENT,), default="600", section="voice", note="quick ack fires when expected extraction exceeds this (ms)"),
    Var("PERSONA_SERVER_ICE", (AGENT,), section="voice", note="server ICE leg: unset = relay-only when TURN is set (ICE-002); 'all' = full list"),
    Var("PERSONA_VOICE_STUB_LLM", (AGENT,), section="voice", forbidden=(AGENT,), note="test-only: canned LLM on the call"),
    Var("PERSONA_VOICE_FAKE_VENDORS", (AGENT,), section="voice", forbidden=(AGENT,), note="test-only: offline tone TTS, no STT"),
    Var("PERSONA_SILENCE_NUDGE_S", (AGENT,), section="voice", default="7", note="silence floor: first nudge"),
    Var("PERSONA_SILENCE_EXAMPLE_S", (AGENT,), section="voice", default="15", note="silence floor: example prompt"),
    Var("PERSONA_SILENCE_PARK_S", (AGENT,), section="voice", default="23", note="silence floor: park the call"),
    Var("PERSONA_CALL_LEASE_TTL_S", (AGENT,), section="voice", default="120", note="call lease TTL"),
    Var("PERSONA_CALL_HEARTBEAT_S", (AGENT,), section="voice", default="30", note="call heartbeat interval"),
    Var("PERSONA_CALL_GRACE_S", (AGENT,), section="voice", default="20", note="reconnect grace window (EC-04)"),
    Var("PERSONA_WARMUP_TIMEOUT_S", (AGENT,), section="voice", default="20",
        note="agent.main boot warmup budget (seconds); best-effort, time-boxed"),
    Var("LOG_LEVEL", (AGENT,), section="build", default="info",
        note="uvicorn/log level for agent.main (info|debug|warning|error)"),
    # --- ICE / TURN ------------------------------------------------------------------------
    Var("CLOUDFLARE_TURN_KEY_ID", (AGENT,), required=(AGENT,), section="ice",
        how="Cloudflare dashboard → Realtime → TURN → create key (see scripts/deploy/cloudflare-turn.md)",
        note="with the token: short-lived TURN creds minted per call for both legs (ADR 0001)"),
    Var("CLOUDFLARE_TURN_API_TOKEN", (AGENT,), required=(AGENT,), secret=True, section="ice",
        how="shown once when the TURN key is created"),
    Var("PERSONA_TURN_URLS", (AGENT,), section="ice",
        note="static TURN alternative (Twilio/metered), comma-separated; used only if Cloudflare vars are unset"),
    Var("PERSONA_TURN_USERNAME", (AGENT,), section="ice", note="static TURN username"),
    Var("PERSONA_TURN_CREDENTIAL", (AGENT,), secret=True, section="ice", note="static TURN credential"),
    Var("PERSONA_STUN_URLS", (AGENT,), section="ice", default="stun:stun.l.google.com:19302"),
    Var("NEXT_PUBLIC_PERSONA_ICE_URLS", (WEB,), section="ice", default="stun:stun.l.google.com:19302",
        note="PUBLIC, inlined at `next build` (set before deploy). Browser fallback ICE; `none` = host-only. Never put TURN creds here"),
    # --- Google OAuth ---------------------------------------------------------------------
    Var("GOOGLE_OAUTH_CLIENT_ID", (AGENT, WEB), required=(AGENT, WEB), section="google",
        how="Google Cloud → APIs & Services → Credentials → OAuth client (Web). See scripts/deploy/google-oauth.md"),
    Var("GOOGLE_OAUTH_CLIENT_SECRET", (AGENT, WEB), required=(AGENT, WEB), secret=True, section="google",
        note="web: code exchange; agent: refresh-token refresh"),
    Var("GOOGLE_OAUTH_REDIRECT_URL", (WEB,), section="google", default="<origin>/api/oauth/google/callback",
        note="pin to `https://<vercel-domain>/api/oauth/google/callback` so preview URLs can't drift"),
    Var("GOOGLE_OAUTH_AUTH_URL", (WEB,), section="google", forbidden=(WEB,),
        default="https://accounts.google.com/o/oauth2/v2/auth", note="test-only override (e2e Google double)"),
    Var("GOOGLE_OAUTH_TOKEN_URL", (WEB,), section="google", forbidden=(WEB,),
        default="https://oauth2.googleapis.com/token", note="test-only override"),
    Var("GOOGLE_OAUTH_ISSUER", (WEB,), section="google", forbidden=(WEB,),
        default="accounts.google.com", note="test-only override"),
    # --- observability ---------------------------------------------------------------------
    Var("PERSONA_TRACING", (AGENT,), section="obs", default="noop", note="noop | jsonl | langfuse"),
    Var("PERSONA_TRACE_FILE", (AGENT,), section="obs", default=".persona-qa/traces.jsonl", note="jsonl mode only"),
    Var("LANGFUSE_PUBLIC_KEY", (AGENT,), section="obs", how="Langfuse project → API keys", note="only if PERSONA_TRACING=langfuse"),
    Var("LANGFUSE_SECRET_KEY", (AGENT,), secret=True, section="obs", how="Langfuse project → API keys"),
    Var("LANGFUSE_HOST", (AGENT,), section="obs", default="https://cloud.langfuse.com"),
    # --- build identity (platform-provided) ---------------------------------------------------
    Var("GIT_SHA", (AGENT,), section="build", note="build id for /health; fly-agent.sh passes `fly deploy --env GIT_SHA=<sha>`"),
    Var("FLY_IMAGE_REF", (PLATFORM,), section="build", note="set by Fly; /health build id fallback"),
    Var("RAILWAY_GIT_COMMIT_SHA", (PLATFORM,), section="build", note="set by Railway (fallback host)"),
    Var("VERCEL_GIT_COMMIT_SHA", (PLATFORM,), section="build", note="set by Vercel on git deploys"),
    Var("PERSONA_BUILD_SHA", (WEB,), section="build", note="`<meta name=build-sha>`; vercel-web.sh passes it as --build-env for CLI deploys"),
    # --- QA / harness / spike (never set in prod) ------------------------------------------------
    Var("PERSONA_TEST_DATABASE_URL", (TEST,), secret=True, section="test", note="server on which tests create/drop scratch DBs"),
    Var("PERSONA_QA_LIVE", (TEST,), section="test", note="`1` = live LLM tier (costs money)"),
    Var("PERSONA_E2E_AGENT_URL", (TEST,), section="test", note="Playwright: real agent instead of e2e/stub-agent.mjs"),
    Var("PERSONA_E2E_WEB_PORT", (TEST,), section="test", note="Playwright: web listen port (default 3100; FE-005 uses 3400 to avoid worker collisions)"),
    Var("PERSONA_E2E_STUB_PORT", (TEST,), section="test", note="Playwright: stub-agent listen port (default 3199)"),
    Var("PERSONA_E2E_HOSTED_WEB_URL", (TEST,), section="test", note="Playwright: live web URL; selects only the hosted probe project (scripts/hosted-e2e.sh); set-but-empty fails"),
    Var("PERSONA_AUDIT_URL", (TEST,), section="test", note="Button audit (qa:audit): LIVE web URL; unset = LOCAL stub target; set-but-empty fails"),
    Var("PERSONA_AUDIT_TARGET", (TEST,), section="test", note="Button audit: set by playwright.audit.config.ts (local|live); not user-set"),
    Var("PERSONA_AUDIT_DOCS", (TEST,), section="test", note="Button audit: `1` = write screenshots + report to docs/qa/button-audit{.md,/}"),
    Var("PERSONA_WEB_URL", (TEST,), section="test", note="Playwright: already-running web (skips build/start)"),
    Var("CI", (TEST,), section="test", note="Playwright: no server reuse"),
    Var("PERSONA_SPIKE_TOKEN", (TEST,), secret=True, section="test", note="INFRA-001 voice spike only"),
    Var("PERSONA_SPIKE_MAX_CALL_SECS", (TEST,), section="test", default="300", note="INFRA-001 voice spike only"),
    Var("HOST", (TEST,), section="test", note="INFRA-001 voice spike bind host"),
    Var("PORT", (TEST,), section="test", note="INFRA-001 voice spike bind port"),
)

BY_NAME: dict[str, Var] = {v.name: v for v in REGISTRY}
TARGETS = (AGENT, WEB, LOCAL)
SECTIONS = {
    "core": "Core wiring", "llm": "LLM (Anthropic)", "voice": "Voice (Deepgram / Cartesia / call policy)",
    "ice": "ICE / TURN", "google": "Google OAuth", "obs": "Observability (Langfuse)",
    "build": "Build identity", "test": "QA / harness / spike only (never set in prod)",
}


def vars_for(target: str) -> list[Var]:
    return [v for v in REGISTRY if target in v.components or target in v.required or target in v.forbidden]


def parse_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines (optional `export `, quotes, # comments). Values are never printed."""
    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        k, v = line.split("=", 1)
        v = v.strip()
        if v.startswith("#"):
            v = ""
        elif v and v[0] in "\"'" and v[-1:] == v[0] and len(v) >= 2:
            v = v[1:-1]
        elif " #" in v:
            v = v.split(" #", 1)[0].rstrip()
        out[k.strip()] = v
    return out


def check(target: str, env: Mapping[str, str], allow_missing: set[str] = frozenset()) -> tuple[list[str], bool]:
    """Return (report lines, ok). Lines contain names and statuses only — never values."""
    lines, ok = [], True
    for v in vars_for(target):
        val = (env.get(v.name) or "").strip()
        if target in v.forbidden:
            if val:
                lines.append(f"FORBIDDEN {v.name} (test-only; unset it for {target})")
                ok = False
            continue
        if target in v.required:
            if not val:
                if v.name in allow_missing:
                    lines.append(f"skipped   {v.name} (required, allowed missing)")
                else:
                    lines.append(f"MISSING   {v.name} (required)")
                    ok = False
            elif v.validate and not v.validate(val):
                lines.append(f"INVALID   {v.name} (required; bad format)")
                ok = False
            else:
                lines.append(f"present   {v.name}")
        else:
            lines.append(f"{'present' if val else 'unset':9} {v.name} (optional)")
    return lines, ok


def emit(target: str, env: Mapping[str, str]) -> list[tuple[str, str]]:
    """(name, value) pairs to push to the host for `target`: known, set, not test-only."""
    out = []
    for v in vars_for(target):
        val = (env.get(v.name) or "").strip()
        if val and target not in v.forbidden and "\n" not in val:
            out.append((v.name, val))
    return out


# --- generated docs ------------------------------------------------------------------------

def _cell(s: str) -> str:
    return s.replace("|", "\\|")


def markdown() -> str:
    comp = {AGENT: "agent (Fly)", WEB: "web (Vercel)", LOCAL: "local", TEST: "test", PLATFORM: "platform"}
    out = [
        "# Env contract",
        "",
        "<!-- GENERATED by `python3 scripts/check-env.py --write` from REGISTRY. Do not edit by hand. -->",
        "",
        "Every environment variable read by `services/agent/agent` and `apps/web` (excluding e2e), plus",
        "QA-only knobs. `harness/tests/test_check_env.py` fails if code reads a name not listed here.",
        "",
        "- **Required** lists the `check-env.py --target` that fails without it.",
        "- **Secret** vars go to `fly secrets import` / Vercel encrypted env; never commit or print them.",
        "- Non-secret agent defaults live in `infra/fly.toml` `[env]`.",
        "- `NEXT_PUBLIC_*` is inlined into the browser bundle at build time — public by definition.",
        "- *Forbidden* = test doubles that `check-env.py` rejects on that deploy target.",
        "- Renamed from the old scaffold `.env.example`: `DATABASE_URL` → `PERSONA_DATABASE_URL`,",
        "  `TURN_URLS/USERNAME/CREDENTIAL` → `PERSONA_TURN_*` (old names are read by nothing).",
        "",
        "Check a target by name only (never prints values):",
        "`python3 scripts/check-env.py --target agent --env-file .persona-deploy/agent.env`",
        "",
    ]
    for key, title in SECTIONS.items():
        rows = [v for v in REGISTRY if v.section == key]
        if not rows:
            continue
        out += [f"## {title}", "", "| Variable | Component | Required | Secret | Default | How to get / generate | Notes |",
                "|---|---|---|---|---|---|---|"]
        for v in rows:
            req = ", ".join(v.required) or "optional"
            notes = v.note + (f" **Forbidden on {', '.join(v.forbidden)}.**" if v.forbidden else "")
            out.append("| `{}` | {} | {} | {} | {} | {} | {} |".format(
                v.name, ", ".join(comp[c] for c in v.components), req,
                "secret" if v.secret else ("public" if v.name.startswith("NEXT_PUBLIC_") else "no"),
                _cell(f"`{v.default}`" if v.default else "—"), _cell(v.how or "—"), _cell(notes.strip() or "—")))
        out.append("")
    return "\n".join(out)


EXAMPLES = {
    "root": (ROOT / ".env.example", (AGENT, WEB, LOCAL)),
    "agent": (ROOT / "services/agent/.env.example", (AGENT,)),
    "web": (ROOT / "apps/web/.env.example", (WEB,)),
}


def example(kind: str) -> str:
    _, comps = EXAMPLES[kind]
    head = {
        "root": "# All env var NAMES (no values). Generated by scripts/check-env.py --write; see docs/deploy/ENV.md.\n"
                "# Per-component files: services/agent/.env.example (Fly), apps/web/.env.example (Vercel).\n"
                "# Real values live in gitignored files (.persona-deploy/*.env, .persona-local/) or host secret stores.\n",
        "agent": "# services/agent on Fly.io — NAMES only. Generated by scripts/check-env.py --write; see docs/deploy/ENV.md.\n"
                 "# Fill a copy at .persona-deploy/agent.env (gitignored), then scripts/deploy/fly-agent.sh.\n",
        "web": "# apps/web on Vercel — NAMES only. Generated by scripts/check-env.py --write; see docs/deploy/ENV.md.\n"
               "# Fill a copy at .persona-deploy/web.env (gitignored), then scripts/deploy/vercel-web.sh.\n",
    }[kind]
    lines = [head.rstrip("\n")]
    for key, title in SECTIONS.items():
        if key == "test":
            continue
        rows = [v for v in REGISTRY if v.section == key and set(v.components) & set(comps) and not set(v.forbidden) & set(comps)]
        if not rows:
            continue
        lines += ["", f"# --- {title}"]
        for v in rows:
            tags = ["required" if set(v.required) & set(comps) else "optional"]
            if v.secret:
                tags.append("secret")
            if kind == "root":
                tags.append("/".join(c for c in v.components if c in comps))
            if v.default:
                tags.append(f"default {v.default}")
            lines.append(f"{v.name}=  # {', '.join(tags)}")
    return "\n".join(lines) + "\n"


def write_all() -> None:
    (ROOT / "docs/deploy").mkdir(parents=True, exist_ok=True)
    (ROOT / "docs/deploy/ENV.md").write_text(markdown())
    for kind, (path, _) in EXAMPLES.items():
        path.write_text(example(kind))


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--target", choices=TARGETS)
    g.add_argument("--markdown", action="store_true")
    g.add_argument("--example", choices=sorted(EXAMPLES))
    g.add_argument("--write", action="store_true")
    g.add_argument("--emit", choices=(AGENT, WEB))
    g.add_argument("--names", choices=(AGENT, WEB), help="names --emit would output (no values)")
    p.add_argument("--env-file", type=Path, help="read values from this file instead of the process env")
    p.add_argument("--allow-missing", action="append", default=[], metavar="NAME")
    a = p.parse_args(argv)
    if a.markdown:
        print(markdown(), end="")
        return 0
    if a.example:
        print(example(a.example), end="")
        return 0
    if a.write:
        write_all()
        return 0
    if a.emit or a.names:
        if not a.env_file or not a.env_file.is_file():
            print("check-env: --emit/--names need an existing --env-file", file=sys.stderr)
            return 2
        for name, val in emit(a.emit or a.names, parse_env_file(a.env_file)):
            print(f"{name}={val}" if a.emit else name)
        return 0
    if a.env_file:
        if not a.env_file.is_file():
            print(f"check-env: env file not found: {a.env_file}", file=sys.stderr)
            return 2
        env = parse_env_file(a.env_file)
    else:
        env = dict(os.environ)
    lines, ok = check(a.target, env, set(a.allow_missing))
    print(f"check-env --target {a.target}" + (f" (file {a.env_file.name})" if a.env_file else " (process env)"))
    for line in lines:
        print("  " + line)
    print(f"RESULT: {'OK' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
