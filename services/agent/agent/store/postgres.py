"""psycopg (v3, sync) store. FastAPI runs these calls in its threadpool."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
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
    """A live call already holds this session's voice lease (API maps to 409)."""


@dataclass(frozen=True)
class StoredEvent:
    id: int
    at: datetime
    channel: Optional[str]
    kind: str
    payload: dict
    trace_id: Optional[str]


@dataclass(frozen=True)
class CallLease:
    call_id: str
    expires_at: datetime


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
                conn.execute(
                    "insert into gmail_connections (session_id, google_sub, email, scopes)"
                    " values (%s, %s, %s, %s) on conflict (session_id) do update set"
                    " google_sub = excluded.google_sub, email = excluded.email,"
                    " scopes = excluded.scopes, connected_at = now()",
                    (sid, gmail["google_sub"], gmail["email"], list(gmail["scopes"])),
                )
        return row["version"]

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

    def acquire_call_lease(self, session_id: str, *, ttl_s: float) -> CallLease:
        sid = _uuid(session_id)
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute("select id from sessions where id = %s for update", (sid,)).fetchone()
            if row is None:
                raise NotFoundError(session_id)
            call_id = conn.execute("insert into calls (session_id) values (%s) returning id", (sid,)).fetchone()["id"]
            got = conn.execute(
                "update sessions set call_lease_holder = %s,"
                " call_lease_expires_at = now() + make_interval(secs => %s)"
                " where id = %s and (call_lease_holder is null or call_lease_expires_at <= now())"
                " returning call_lease_expires_at",
                (str(call_id), ttl_s, sid),
            ).fetchone()
            if got is None:
                raise LeaseHeldError(session_id)  # rolls back the calls row too
        return CallLease(str(call_id), got["call_lease_expires_at"])

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

