"""psycopg (v3, sync) store. FastAPI runs these calls in its threadpool."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from ..brain.state import SessionState
from .codec import BRAIN_COLUMNS, state_from_row, state_to_columns

MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "infra" / "supabase" / "migrations"


class NotFoundError(LookupError):
    pass


class VersionConflictError(RuntimeError):
    """Another writer committed first; reload and retry (API maps to 409)."""

    def __init__(self, session_id: str, expected: int):
        super().__init__(f"session {session_id}: version {expected} is stale")
        self.session_id, self.expected = session_id, expected


class LeaseHeldError(RuntimeError):
    """A live call already holds this session's voice lease (API maps to 409 with a
    take-over option)."""

    def __init__(self, session_id: str, call_id: Optional[str] = None):
        super().__init__(f"session {session_id}: call {call_id} holds the lease")
        self.session_id, self.call_id = session_id, call_id


@dataclass(frozen=True)
class StoredEvent:
    id: int
    at: datetime
    channel: Optional[str]
    kind: str
    payload: dict
    trace_id: Optional[str]


@dataclass(frozen=True)
class GmailConnection:
    session_id: str
    google_sub: str
    email: str
    scopes: list[str]
    token_status: str
    refresh_token_enc: Optional[bytes] = field(default=None, repr=False)  # ciphertext; never logged
    token_key_id: Optional[str] = None
    connected_at: Optional[datetime] = None
    last_refresh_at: Optional[datetime] = None
    last_error: Optional[str] = None
    revoked_at: Optional[datetime] = None


@dataclass(frozen=True)
class CallLease:
    call_id: str
    expires_at: datetime
    replaced: Optional[str] = None     # call id this lease took over from (EC-02)


def apply_migrations(dsn: str, directory: Path = MIGRATIONS_DIR) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        for path in sorted(directory.glob("*.sql")):
            conn.execute(path.read_text())


def _uuid(session_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(session_id))
    except ValueError:
        raise NotFoundError(session_id) from None


class PgStore:
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10):
        self.pool = ConnectionPool(
            dsn, min_size=min_size, max_size=max_size, kwargs={"row_factory": dict_row}, open=True
        )

    def close(self) -> None:
        self.pool.close()

    def ping(self) -> bool:
        with self.pool.connection() as conn:
            return conn.execute("select 1 as ok").fetchone()["ok"] == 1

    # --- sessions ----------------------------------------------------------

    def create_session(self, *, flow_version: int, token_hash: str) -> SessionState:
        with self.pool.connection() as conn:
            row = conn.execute(
                "insert into sessions (flow_version, token_hash) values (%s, %s) returning *",
                (flow_version, token_hash),
            ).fetchone()
        return state_from_row(row)

    def load(self, session_id: str) -> SessionState:
        return state_from_row(self._row(session_id))

    def token_hash(self, session_id: str) -> Optional[str]:
        return self._row(session_id)["token_hash"]

    def lease(self, session_id: str) -> Optional[CallLease]:
        """The live call lease, if any (expired leases are treated as free)."""
        with self.pool.connection() as conn:
            row = conn.execute(
                "select call_lease_holder, call_lease_expires_at, call_lease_expires_at > now() as live"
                " from sessions where id = %s",
                (_uuid(session_id),),
            ).fetchone()
        if row is None:
            raise NotFoundError(session_id)
        return CallLease(row["call_lease_holder"], row["call_lease_expires_at"]) if row["live"] else None

    def _row(self, session_id: str) -> dict[str, Any]:
        with self.pool.connection() as conn:
            row = conn.execute("select * from sessions where id = %s", (_uuid(session_id),)).fetchone()
        if row is None:
            raise NotFoundError(session_id)
        return row

    def commit(
        self,
        state: SessionState,
        *,
        expected_version: int,
        events: Iterable[dict],
        gmail: Optional[dict] = None,
    ) -> int:
        """Write brain-owned columns iff `version` is still `expected_version`, append
        `events` and (optionally) upsert the gmail connection — one transaction.
        Returns the new version."""
        sid = _uuid(state.session_id)
        cols = state_to_columns(state)
        casts = {"status": "::session_status", "active_channel": "::channel"}
        sets = ", ".join(f"{c} = %({c})s{casts.get(c, '')}" for c in BRAIN_COLUMNS)
        params = {
            **cols,
            "slots": Jsonb(cols["slots"]),
            "node_attempts": Jsonb(cols["node_attempts"]),
            "id": sid,
            "expected": expected_version,
        }
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute(
                f"update sessions set {sets}, version = version + 1, updated_at = now(),"
                " graduated_at = case when %(status)s::session_status = 'graduated' then coalesce(graduated_at, now()) end"
                " where id = %(id)s and version = %(expected)s returning version",
                params,
            ).fetchone()
            if row is None:
                if conn.execute("select 1 from sessions where id = %s", (sid,)).fetchone() is None:
                    raise NotFoundError(state.session_id)
                raise VersionConflictError(state.session_id, expected_version)
            self._insert_events(conn, sid, events)
            if gmail is not None:
                self._upsert_gmail(conn, sid, gmail)
        return row["version"]

    # --- gmail connection ----------------------------------------------------

    @staticmethod
    def _upsert_gmail(conn, sid: uuid.UUID, gmail: dict) -> None:
        """`gmail` carries ciphertext only (`refresh_token_enc`); plaintext never reaches the store.
        A reconnect without a new refresh token keeps the old one iff it's the same Google account."""
        enc = gmail.get("refresh_token_enc")
        conn.execute(
            "insert into gmail_connections (session_id, google_sub, email, scopes, refresh_token_enc,"
            " token_key_id, token_status) values (%(sid)s, %(sub)s, %(email)s, %(scopes)s, %(enc)s, %(kid)s, %(status)s)"
            " on conflict (session_id) do update set"
            " refresh_token_enc = case when excluded.refresh_token_enc is not null then excluded.refresh_token_enc"
            "   when gmail_connections.google_sub = excluded.google_sub and gmail_connections.token_status = 'stored'"
            "   then gmail_connections.refresh_token_enc end,"
            " token_key_id = case when excluded.refresh_token_enc is not null then excluded.token_key_id"
            "   when gmail_connections.google_sub = excluded.google_sub and gmail_connections.token_status = 'stored'"
            "   then gmail_connections.token_key_id end,"
            " token_status = case when excluded.refresh_token_enc is not null then excluded.token_status"
            "   when gmail_connections.google_sub = excluded.google_sub and gmail_connections.token_status = 'stored'"
            "   then 'stored' else excluded.token_status end,"
            " google_sub = excluded.google_sub, email = excluded.email, scopes = excluded.scopes,"
            " connected_at = now(), last_error = null, revoked_at = null",
            {"sid": sid, "sub": gmail["google_sub"], "email": gmail["email"], "scopes": list(gmail["scopes"]),
             "enc": enc, "kid": gmail.get("token_key_id") if enc is not None else None,
             "status": gmail.get("token_status") or ("stored" if enc is not None else "missing")},
        )

    def gmail_connection(self, session_id: str) -> Optional[GmailConnection]:
        with self.pool.connection() as conn:
            row = conn.execute(
                "select * from gmail_connections where session_id = %s", (_uuid(session_id),)).fetchone()
        if row is None:
            return None
        row["session_id"] = str(row["session_id"])
        row["refresh_token_enc"] = bytes(row["refresh_token_enc"]) if row["refresh_token_enc"] is not None else None
        return GmailConnection(**row)

    def mark_gmail_refreshed(self, session_id: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("update gmail_connections set last_refresh_at = now(), last_error = null"
                         " where session_id = %s", (_uuid(session_id),))

    def mark_gmail_invalid(self, session_id: str, *, error: str) -> None:
        """Google rejected the refresh token (invalid_grant / revoked): drop it, ask for reconnect."""
        with self.pool.connection() as conn:
            conn.execute("update gmail_connections set token_status = 'invalid', refresh_token_enc = null,"
                         " token_key_id = null, last_error = %s where session_id = %s and token_status <> 'revoked'",
                         (error[:64], _uuid(session_id)))

    def revoke_gmail(self, session_id: str) -> bool:
        """Clear tokens + set revoked_at. Idempotent: returns True only on the first revoke."""
        with self.pool.connection() as conn:
            row = conn.execute(
                "update gmail_connections set refresh_token_enc = null, token_key_id = null,"
                " token_status = 'revoked', revoked_at = coalesce(revoked_at, now())"
                " where session_id = %s and revoked_at is null returning session_id",
                (_uuid(session_id),),
            ).fetchone()
        return row is not None

    # --- events ------------------------------------------------------------

    def append_events(self, session_id: str, events: Iterable[dict]) -> None:
        """Events that don't change brain state (e.g. call lease bookkeeping)."""
        with self.pool.connection() as conn, conn.transaction():
            self._insert_events(conn, _uuid(session_id), events)

    @staticmethod
    def _insert_events(conn, sid: uuid.UUID, events: Iterable[dict]) -> None:
        rows = [
            (sid, e.get("channel"), e["kind"], Jsonb(e.get("payload") or {}), e.get("trace_id"))
            for e in events
        ]
        if rows:
            with conn.cursor() as cur:
                cur.executemany(
                    "insert into session_events (session_id, channel, kind, payload, trace_id)"
                    " values (%s, %s, %s, %s, %s)",
                    rows,
                )

    def events_after(
        self, session_id: str, after_id: int = 0, *, kinds: Optional[list[str]] = None, limit: int = 200
    ) -> list[StoredEvent]:
        q = "select id, at, channel, kind, payload, trace_id from session_events where session_id = %s and id > %s"
        args: list[Any] = [_uuid(session_id), after_id]
        if kinds:
            q += " and kind = any(%s)"
            args.append(kinds)
        q += " order by id limit %s"
        args.append(limit)
        with self.pool.connection() as conn:
            return [StoredEvent(**r) for r in conn.execute(q, args).fetchall()]

    # --- call lease ----------------------------------------------------------

    def acquire_call_lease(self, session_id: str, *, ttl_s: float, take_over: bool = False) -> CallLease:
        """New call + lease. A live lease blocks (LeaseHeldError) unless `take_over`, which
        ends the old call (`taken_over`) and hands the lease to the new one atomically."""
        sid = _uuid(session_id)
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "select call_lease_holder, call_lease_expires_at > now() as live from sessions"
                " where id = %s for update",
                (sid,),
            ).fetchone()
            if row is None:
                raise NotFoundError(session_id)
            held = row["call_lease_holder"] if row["live"] else None
            if held and not take_over:
                raise LeaseHeldError(session_id, held)
            if held:
                conn.execute(
                    "update calls set ended_at = coalesce(ended_at, now()),"
                    " end_reason = coalesce(end_reason, 'taken_over') where id = %s and session_id = %s",
                    (_uuid(held), sid),
                )
            call_id = conn.execute("insert into calls (session_id) values (%s) returning id", (sid,)).fetchone()["id"]
            got = conn.execute(
                "update sessions set call_lease_holder = %s,"
                " call_lease_expires_at = now() + make_interval(secs => %s)"
                " where id = %s returning call_lease_expires_at",
                (str(call_id), ttl_s, sid),
            ).fetchone()
        return CallLease(str(call_id), got["call_lease_expires_at"], replaced=held)

    def renew_call_lease(self, session_id: str, call_id: str, *, ttl_s: float,
                         reconnect: bool = False) -> Optional[CallLease]:
        """Heartbeat / grace / reconnect: set the lease to expire `ttl_s` from now iff
        `call_id` still holds a live lease. None = lost (taken over, released, expired)."""
        sid = _uuid(session_id)
        with self.pool.connection() as conn, conn.transaction():
            got = conn.execute(
                "update sessions set call_lease_expires_at = now() + make_interval(secs => %s)"
                " where id = %s and call_lease_holder = %s and call_lease_expires_at > now()"
                " returning call_lease_expires_at",
                (ttl_s, sid, call_id),
            ).fetchone()
            if got is not None and reconnect:
                conn.execute("update calls set reconnects = reconnects + 1 where id = %s and session_id = %s",
                             (_uuid(call_id), sid))
        return CallLease(call_id, got["call_lease_expires_at"]) if got else None

    def lease_holder(self, session_id: str) -> Optional[str]:
        """The lease holder even if expired (orphaned-call reconciliation)."""
        return self._row(session_id)["call_lease_holder"]

    def release_call_lease(self, session_id: str, call_id: str, *, reason: str) -> bool:
        sid = _uuid(session_id)
        with self.pool.connection() as conn, conn.transaction():
            held = conn.execute(
                "update sessions set call_lease_holder = null, call_lease_expires_at = null"
                " where id = %s and call_lease_holder = %s returning id",
                (sid, call_id),
            ).fetchone()
            conn.execute(
                "update calls set ended_at = coalesce(ended_at, now()), end_reason = coalesce(end_reason, %s)"
                " where id = %s and session_id = %s",
                (reason, _uuid(call_id), sid),
            )
        return held is not None

