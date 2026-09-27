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
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..brain.spec import FlowSpec, load_spec
from ..brain.validators import gmail_oauth
from ..obs.tracing import Tracer, get_tracer
from ..store import LeaseHeldError, NotFoundError, PgStore, VersionConflictError
from .llm import FakeLlm, TurnLlm
from .ratelimit import RateLimiter, SlidingWindowLimiter
from .service import Notifier, SessionService, TurnOutcome

MAX_TEXT_LEN = 2000


@dataclass
class Settings:
    internal_secret: Optional[str] = None     # shared secret for server-to-server routes (gmail)
    call_lease_ttl_s: float = 120.0
    sse_poll_s: float = 1.0                   # DB poll floor when no in-process notify arrives
    sse_keepalive_s: float = 15.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            internal_secret=os.environ.get("PERSONA_INTERNAL_SECRET") or None,
            call_lease_ttl_s=float(os.environ.get("PERSONA_CALL_LEASE_TTL_S", "120")),
        )


class TurnIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_LEN)
    version: Optional[int] = None             # optional client-side optimistic check


class GmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    google_sub: str = Field(min_length=1, max_length=255)
    scopes: list[str] = Field(default_factory=list)


class CallIn(BaseModel):
    sdp: Optional[str] = None                 # SmallWebRTC offer (VOICE-001); ignored here
    type: Optional[str] = None


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
) -> FastAPI:
    settings = settings or Settings()
    notifier = Notifier()
    svc = SessionService(store=store, llm=llm or FakeLlm(), spec=spec or load_spec(),
                         tracer=tracer or get_tracer(), notifier=notifier)
    ip_limiter = ip_limiter or SlidingWindowLimiter(120, 60)
    session_limiter = session_limiter or SlidingWindowLimiter(40, 60)

    app = FastAPI(title="persona-agent", version="0.0.1")
    app.state.service, app.state.settings = svc, settings

    @app.exception_handler(NotFoundError)
    async def _nf(_: Request, __: NotFoundError):
        return JSONResponse({"error": "not_found"}, status_code=404)

    @app.exception_handler(VersionConflictError)
    async def _vc(_: Request, exc: VersionConflictError):
        return JSONResponse({"error": "version_conflict", "detail": str(exc)}, status_code=409)

    @app.exception_handler(LeaseHeldError)
    async def _lh(_: Request, __: LeaseHeldError):
        return JSONResponse({"error": "call_in_progress"}, status_code=409)

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

    @app.post("/v1/sessions/{session_id}/gmail")
    async def post_gmail(session_id: str, body: GmailIn, request: Request,
                         x_persona_internal_secret: Optional[str] = Header(None)):
        if not settings.internal_secret:
            raise HTTPException(503, "gmail_route_disabled")  # fail closed without a configured secret
        if not (x_persona_internal_secret and secrets.compare_digest(x_persona_internal_secret, settings.internal_secret)):
            raise HTTPException(401, "invalid_internal_secret")
        limit(request, session_id)
        if gmail_oauth(body.email, oauth_verified=True).outcome != "ok":
            raise HTTPException(422, "invalid_email")
        outcome = await run_in_threadpool(
            svc.gmail_connected, session_id, email=body.email, google_sub=body.google_sub, scopes=body.scopes)
        return _turn_json(outcome)

    @app.post("/v1/sessions/{session_id}/call", status_code=201)
    async def post_call(session_id: str, request: Request, body: Optional[CallIn] = None,
                        authorization: Optional[str] = Header(None), x_session_token: Optional[str] = Header(None)):
        await authorize(session_id, _bearer(authorization, x_session_token, None))
        limit(request, session_id)
        lease = await run_in_threadpool(svc.acquire_call, session_id, ttl_s=settings.call_lease_ttl_s)
        # Placeholder: VOICE-001 replaces this with the SmallWebRTC answer for body.sdp.
        return {"call_id": lease.call_id, "lease_expires_at": lease.expires_at.isoformat(),
                "answer": None, "status": "lease_acquired"}

    @app.delete("/v1/sessions/{session_id}/call/{call_id}")
    async def end_call(session_id: str, call_id: str, request: Request, body: Optional[CallEndIn] = None,
                       authorization: Optional[str] = Header(None), x_session_token: Optional[str] = Header(None)):
        await authorize(session_id, _bearer(authorization, x_session_token, None))
        limit(request, session_id)
        reason = (body or CallEndIn()).reason
        released = await run_in_threadpool(svc.release_call, session_id, call_id, reason=reason)
        return {"released": released}

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
    return create_app(store=PgStore(dsn), settings=Settings.from_env())
