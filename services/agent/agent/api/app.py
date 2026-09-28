"""FastAPI surface (routes: see README.md in this package).

Run: `uvicorn agent.api.app:create_app_from_env --factory` with PERSONA_DATABASE_URL
(and PERSONA_INTERNAL_SECRET for the gmail route). All dependencies are injectable
via `create_app(...)` so tests use an ephemeral Postgres and FakeLlm.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Awaitable, Callable, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from ..brain.spec import FlowSpec, load_spec
from ..brain.validators import gmail_oauth
from ..gmail import GmailError, GmailService, GoogleOAuth, ReconnectRequired, TokenCipher
from ..obs.tracing import Tracer, get_tracer
from ..store import LeaseHeldError, NotFoundError, PgStore, VersionConflictError
from ..voice.config import VoiceConfig
from ..voice.handoff import GRACE_REASONS, CallControl, HandoffSettings
from ..voice.host import CallHost
from .llm import FakeLlm, TurnLlm
from .ratelimit import RateLimiter, SlidingWindowLimiter
from .service import Notifier, SessionService, TurnOutcome

MAX_TEXT_LEN = 2000


@dataclass
class Settings:
    internal_secret: Optional[str] = None     # shared secret for server-to-server routes (gmail)
    call_lease_ttl_s: float = 120.0
    call_heartbeat_s: float = 30.0
    call_grace_s: float = 20.0                # reconnect grace window (EC-04)
    sse_poll_s: float = 1.0                   # DB poll floor when no in-process notify arrives
    sse_keepalive_s: float = 15.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            internal_secret=os.environ.get("PERSONA_INTERNAL_SECRET") or None,
            call_lease_ttl_s=float(os.environ.get("PERSONA_CALL_LEASE_TTL_S", "120")),
            call_heartbeat_s=float(os.environ.get("PERSONA_CALL_HEARTBEAT_S", "30")),
            call_grace_s=float(os.environ.get("PERSONA_CALL_GRACE_S", "20")),
        )

    def handoff(self) -> HandoffSettings:
        return HandoffSettings(lease_ttl_s=self.call_lease_ttl_s, heartbeat_s=self.call_heartbeat_s,
                               grace_s=self.call_grace_s)


class TurnIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_LEN)
    version: Optional[int] = None             # optional client-side optimistic check


class GmailFailedIn(BaseModel):
    reason: str = Field(default="cancelled", pattern=r"^[a-z_]{1,32}$")   # e.g. cancelled | window_closed | access_denied


class GmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    google_sub: str = Field(min_length=1, max_length=255)
    scopes: list[str] = Field(default_factory=list)
    # Optional: FE-003 may still stub it. Encrypted before it touches the store; never logged/echoed.
    refresh_token: Optional[str] = Field(default=None, max_length=2048, repr=False)


class CallIn(BaseModel):
    sdp: Optional[str] = Field(default=None, max_length=20_000)  # SmallWebRTC offer (non-trickle)
    type: Optional[str] = None
    take_over: bool = False                   # explicit user confirm after a 409 call_in_progress (EC-02)
    resume_call_id: Optional[str] = None      # reconnect inside the grace window (EC-04)


class CallEndIn(BaseModel):
    reason: str = "user_hangup"


@lru_cache(maxsize=1)
def git_sha() -> Optional[str]:
    for var in ("GIT_SHA", "FLY_IMAGE_REF", "RAILWAY_GIT_COMMIT_SHA", "VERCEL_GIT_COMMIT_SHA"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=2)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _turn_json(o: TurnOutcome) -> dict:
    return {"reply": o.reply, "state": o.snapshot, "push_ui": o.plan.push_ui, "trace_id": o.trace_id}


def _bearer(authorization: Optional[str], x_session_token: Optional[str], query: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return x_session_token or query


def create_app(
    *,
    store: PgStore,
    llm: Optional[TurnLlm] = None,
    spec: Optional[FlowSpec] = None,
    tracer: Optional[Tracer] = None,
    settings: Optional[Settings] = None,
    ip_limiter: Optional[RateLimiter] = None,
    session_limiter: Optional[RateLimiter] = None,
    gmail: Optional[GmailService] = None,
    voice: Optional[VoiceConfig] = None,
    call_host: Optional[CallHost] = None,
    ice: Optional[Callable[[], Awaitable[tuple[list[dict], Optional[int]]]]] = None,
    ice_limiter: Optional[RateLimiter] = None,
) -> FastAPI:
    """`voice=None` (default) keeps the call route lease-only (`answer: null`); pass
    `VoiceConfig.from_env()` (or a `call_host`) to host the pipeline and answer the SDP."""
    settings = settings or Settings()
    notifier = Notifier()
    svc = SessionService(store=store, llm=llm or FakeLlm(), spec=spec or load_spec(),
                         tracer=tracer or get_tracer(), notifier=notifier)
    ip_limiter = ip_limiter or SlidingWindowLimiter(120, 60)
    session_limiter = session_limiter or SlidingWindowLimiter(40, 60)

    ice_limiter = ice_limiter or SlidingWindowLimiter(20, 60)

    calls = CallControl(svc, settings.handoff())
    if call_host is None and voice is not None and voice.can_answer:
        call_host = CallHost(calls, voice, service=svc, spec=svc.spec)
    if ice is None:
        from ..voice.ice import resolve_ice as ice

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        # Shutdown: hang up live calls (lease released, chat resumes), stop grace timers.
        if app.state.call_host is not None:
            await app.state.call_host.close()
        await calls.close()

    app = FastAPI(title="persona-agent", version="0.0.1", lifespan=lifespan)
    gmail = gmail or GmailService(store=store, cipher=None, oauth=None)
    app.state.service, app.state.settings, app.state.calls, app.state.gmail = svc, settings, calls, gmail
    app.state.call_host = call_host

    @app.exception_handler(NotFoundError)
    async def _nf(_: Request, __: NotFoundError):
        return JSONResponse({"error": "not_found"}, status_code=404)

    @app.exception_handler(VersionConflictError)
    async def _vc(_: Request, exc: VersionConflictError):
        return JSONResponse({"error": "version_conflict", "detail": str(exc)}, status_code=409)

    @app.exception_handler(LeaseHeldError)
    async def _lh(_: Request, __: LeaseHeldError):
        # Tab B: "call already in progress" + explicit take-over (retry with take_over=true).
        return JSONResponse({"error": "call_in_progress", "can_take_over": True}, status_code=409)

    @app.exception_handler(ReconnectRequired)
    async def _rc(_: Request, exc: ReconnectRequired):
        return JSONResponse({"error": "gmail_reconnect_required", "reason": exc.reason}, status_code=409)

    @app.exception_handler(GmailError)
    async def _ge(_: Request, exc: GmailError):
        status = exc.status if exc.status in (403, 503) else 502
        return JSONResponse({"error": exc.code}, status_code=status)

    def limit(request: Request, session_id: Optional[str] = None) -> None:
        ip = request.client.host if request.client else "unknown"
        if not ip_limiter.allow(f"ip:{ip}") or (session_id and not session_limiter.allow(f"s:{session_id}")):
            raise HTTPException(429, "rate_limited")

    async def authorize(session_id: str, token: Optional[str]) -> None:
        if not await run_in_threadpool(svc.check_token, session_id, token):
            raise HTTPException(401, "invalid_session_token")

    @app.get("/health")
    def health():
        try:
            db = "ok" if store.ping() else "error"
        except Exception:  # noqa: BLE001 — health must answer, not raise
            db = "error"
        return {"ok": True, "git_sha": git_sha(), "db": db, "flow_version": svc.spec.raw["version"]}

    @app.post("/v1/sessions", status_code=201)
    def create_session(request: Request):
        limit(request)
        sid, token, outcome = svc.create()
        return {"id": sid, "token": token, **_turn_json(outcome)}

    @app.get("/v1/sessions/{session_id}")
    async def get_session(session_id: str, token: Optional[str] = None,
                          authorization: Optional[str] = Header(None), x_session_token: Optional[str] = Header(None)):
        await authorize(session_id, _bearer(authorization, x_session_token, token))
        return await run_in_threadpool(svc.get_snapshot, session_id)

    @app.post("/v1/sessions/{session_id}/turns")
    async def post_turn(session_id: str, body: TurnIn, request: Request,
                        authorization: Optional[str] = Header(None), x_session_token: Optional[str] = Header(None)):
        await authorize(session_id, _bearer(authorization, x_session_token, None))
        limit(request, session_id)
        outcome = await run_in_threadpool(svc.text_turn, session_id, body.text, expected_version=body.version)
        return _turn_json(outcome)

    def require_internal(secret: Optional[str]) -> None:
        if not settings.internal_secret:
            raise HTTPException(503, "gmail_route_disabled")  # fail closed without a configured secret
        if not (secret and secrets.compare_digest(secret, settings.internal_secret)):
            raise HTTPException(401, "invalid_internal_secret")

    @app.post("/v1/sessions/{session_id}/gmail")
    async def post_gmail(session_id: str, body: GmailIn, request: Request,
                         x_persona_internal_secret: Optional[str] = Header(None)):
        require_internal(x_persona_internal_secret)
        limit(request, session_id)
        if gmail_oauth(body.email, oauth_verified=True).outcome != "ok":
            raise HTTPException(422, "invalid_email")
        await run_in_threadpool(store.load, session_id)  # 404 before sealing a token for a bogus session
        sealed = gmail.seal(session_id, body.refresh_token)
        outcome = await run_in_threadpool(
            svc.gmail_connected, session_id, email=body.email, google_sub=body.google_sub, scopes=body.scopes,
            sealed=sealed)
        conn = await run_in_threadpool(store.gmail_connection, session_id)
        return {**_turn_json(outcome), "gmail": {"token_status": conn.token_status if conn else "missing"}}

    @app.delete("/v1/sessions/{session_id}/gmail")
    async def delete_gmail(session_id: str, request: Request,
                           x_persona_internal_secret: Optional[str] = Header(None)):
        """Disconnect: Google revoke (best effort) + clear stored tokens. Idempotent."""
        require_internal(x_persona_internal_secret)
        limit(request, session_id)
        await run_in_threadpool(store.load, session_id)
        revoked = await run_in_threadpool(gmail.disconnect, session_id)
        return {"revoked": revoked, "token_status": "revoked"}

    @app.post("/v1/sessions/{session_id}/gmail/failed")
    async def gmail_failed(session_id: str, body: GmailFailedIn, request: Request,
                           authorization: Optional[str] = Header(None), x_session_token: Optional[str] = Header(None)):
        """Browser: Google consent didn't finish (EC-21). Never changes state; a live call on
        the gmail step offers retry / type-it / skip (VOICE-004)."""
        await authorize(session_id, _bearer(authorization, x_session_token, None))
        limit(request, session_id)
        return {"noticed": await run_in_threadpool(svc.gmail_failed, session_id, reason=body.reason)}

    @app.get("/v1/sessions/{session_id}/gmail/demo")
    async def gmail_demo(session_id: str, request: Request,
                         authorization: Optional[str] = Header(None), x_session_token: Optional[str] = Header(None)):
        """Read-only value demo: 1–3 recent inbox messages (from/subject/snippet/date only)."""
        await authorize(session_id, _bearer(authorization, x_session_token, None))
        limit(request, session_id)
        return {"messages": await run_in_threadpool(gmail.demo, session_id)}

    @app.post("/v1/sessions/{session_id}/call", status_code=201)
    async def post_call(session_id: str, request: Request, body: Optional[CallIn] = None,
                        authorization: Optional[str] = Header(None), x_session_token: Optional[str] = Header(None)):
        await authorize(session_id, _bearer(authorization, x_session_token, None))
        limit(request, session_id)
        body = body or CallIn()
        host: Optional[CallHost] = app.state.call_host
        hosting = host is not None and host.enabled and bool(body.sdp)
        if hosting and (body.type or "offer") != "offer":
            raise HTTPException(422, "sdp_type_must_be_offer")
        if body.resume_call_id:
            lease = await calls.reconnect(session_id, body.resume_call_id)
            if lease is None:  # grace window closed (or taken over): the client starts a new call
                return JSONResponse({"error": "call_ended", "call_id": body.resume_call_id}, status_code=409)
            status = "resumed"
        else:
            lease = await calls.start(session_id, take_over=body.take_over)
            status = "taken_over" if lease.replaced else "lease_acquired"
        answer = None
        if hosting:
            # Take-over already stopped the replaced pipeline (CallControl.start); a resume
            # retires any pipeline still running for this call_id (CallHost.answer).
            try:
                answer = await host.answer(session_id, lease.call_id, body.sdp, "offer",
                                                reconnect=status == "resumed")
            except Exception as e:  # noqa: BLE001 - never hold a lease for a call that can't connect
                logger.warning(f"call {lease.call_id}: SDP answer failed: {type(e).__name__}: {e}")
                await calls.end(session_id, lease.call_id, "answer_failed")
                return JSONResponse({"error": "call_setup_failed", "call_id": lease.call_id}, status_code=502)
        return JSONResponse({"call_id": lease.call_id, "lease_expires_at": lease.expires_at.isoformat(),
                             "answer": answer, "status": status, "replaced": lease.replaced,
                             "grace_s": settings.call_grace_s, "heartbeat_s": settings.call_heartbeat_s},
                            status_code=200 if status == "resumed" else 201)

    @app.get("/v1/sessions/{session_id}/ice")
    async def get_ice(session_id: str, request: Request,
                      authorization: Optional[str] = Header(None), x_session_token: Optional[str] = Header(None)):
        """ICE servers for the browser leg (same source as the server leg; ADR 0001). Minted
        per request when Cloudflare TURN is configured. Credentials are returned, never logged."""
        await authorize(session_id, _bearer(authorization, x_session_token, None))
        limit(request, session_id)
        if not ice_limiter.allow(f"ice:{session_id}"):
            raise HTTPException(429, "rate_limited")
        servers, ttl = await ice()
        return JSONResponse({"ice_servers": servers, "ttl_s": ttl}, headers={"Cache-Control": "no-store"})

    @app.post("/v1/sessions/{session_id}/call/{call_id}/heartbeat")
    async def heartbeat_call(session_id: str, call_id: str,
                             authorization: Optional[str] = Header(None), x_session_token: Optional[str] = Header(None)):
        await authorize(session_id, _bearer(authorization, x_session_token, None))
        if not await calls.heartbeat(session_id, call_id):
            return JSONResponse({"error": "call_lease_lost", "call_id": call_id}, status_code=409)
        return {"ok": True}

    @app.delete("/v1/sessions/{session_id}/call/{call_id}")
    async def end_call(session_id: str, call_id: str, request: Request, body: Optional[CallEndIn] = None,
                       authorization: Optional[str] = Header(None), x_session_token: Optional[str] = Header(None)):
        await authorize(session_id, _bearer(authorization, x_session_token, None))
        limit(request, session_id)
        reason = (body or CallEndIn()).reason
        if reason in GRACE_REASONS:
            # Media lost, not a hangup: keep the call for the grace window (EC-04).
            return {"released": False, "in_grace": await calls.dropped(session_id, call_id),
                    "grace_s": settings.call_grace_s}
        released, outcome = await calls.end(session_id, call_id, reason)
        return {"released": released, **(_turn_json(outcome) if outcome is not None else {})}

    @app.get("/v1/sessions/{session_id}/events")
    async def events(session_id: str, request: Request, token: Optional[str] = None,
                     last_event_id: Optional[int] = None,
                     authorization: Optional[str] = Header(None), x_session_token: Optional[str] = Header(None),
                     last_event_id_header: Optional[str] = Header(None, alias="Last-Event-ID")):
        # EventSource can't set headers, so `?token=` is accepted here (and on the snapshot).
        await authorize(session_id, _bearer(authorization, x_session_token, token))
        after = int(last_event_id_header) if (last_event_id_header or "").isdigit() else (last_event_id or 0)
        return StreamingResponse(
            _stream(request, session_id, after),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def _stream(request: Request, session_id: str, after: int):
        waiter = notifier.subscribe(session_id)
        last_sent = time.monotonic()
        try:
            yield "retry: 2000\n\n"
            while not await request.is_disconnected():
                waiter[1].clear()
                batch = await run_in_threadpool(store.events_after, session_id, after, kinds=["ui_push"])
                for e in batch:
                    after = e.id
                    data = json.dumps(e.payload.get("data", {}), default=str)
                    yield f"id: {e.id}\nevent: {e.payload.get('type', 'message')}\ndata: {data}\n\n"
                    last_sent = time.monotonic()
                if batch:
                    continue
                if time.monotonic() - last_sent >= settings.sse_keepalive_s:
                    yield ": keepalive\n\n"
                    last_sent = time.monotonic()
                try:
                    await asyncio.wait_for(waiter[1].wait(), timeout=settings.sse_poll_s)
                except asyncio.TimeoutError:
                    pass
        finally:
            notifier.unsubscribe(session_id, waiter)

    return app


def create_app_from_env() -> FastAPI:
    dsn = os.environ["PERSONA_DATABASE_URL"]
    store = PgStore(dsn)
    gmail = GmailService(store=store, cipher=TokenCipher.from_env(), oauth=GoogleOAuth.from_env())
    from .claude_llm import llm_from_env  # Claude for the text channel when keyed (live test 2026-09-27)

    return create_app(store=store, llm=llm_from_env(load_spec()), settings=Settings.from_env(),
                      gmail=gmail, voice=VoiceConfig.from_env())
