"""Postgres persistence for session state (single writer: services/agent).

Schema: infra/supabase/migrations/*.sql. Every brain write is an optimistic
`UPDATE sessions ... WHERE id = $1 AND version = $n` plus its `session_events`
in one transaction.
"""
from .codec import state_from_row, state_to_columns  # noqa: F401
from .postgres import (  # noqa: F401
    CallLease,
    LeaseHeldError,
    NotFoundError,
    PgStore,
    StoredEvent,
    VersionConflictError,
    apply_migrations,
)
