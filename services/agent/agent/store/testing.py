"""Ephemeral Postgres for tests (no cloud, no Docker required).

Resolution order:
  1. PERSONA_TEST_DATABASE_URL — a server we may create/drop databases on; a fresh
     `persona_test_<hex>` database is created per test process and dropped at exit.
  2. Local `initdb` + `pg_ctl` on PATH — a throwaway cluster in a temp dir on a free
     127.0.0.1 port, trust auth, removed at exit.
Otherwise `ephemeral_dsn()` returns None and callers skip.

Migrations (infra/supabase/migrations/*.sql) are applied once; `reset()` truncates.
"""
from __future__ import annotations

import atexit
import os
import shutil
import socket
import subprocess
import tempfile
import uuid
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import psycopg

from .postgres import apply_migrations

_dsn: Optional[str] = None
_tried = False


def ephemeral_dsn() -> Optional[str]:
    global _dsn, _tried
    if not _tried:
        _tried = True
        url = os.environ.get("PERSONA_TEST_DATABASE_URL")
        _dsn = _fresh_database(url) if url else _local_cluster()
        if _dsn:
            apply_migrations(_dsn)
    return _dsn


def reset(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("truncate sessions, session_events, calls, gmail_connections restart identity cascade")


def _fresh_database(url: str) -> str:
    name = f"persona_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f'create database "{name}"')

    def drop() -> None:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f'drop database if exists "{name}" with (force)')

    atexit.register(drop)
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{name}"))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _local_cluster() -> Optional[str]:
    initdb, pg_ctl = shutil.which("initdb"), shutil.which("pg_ctl")
    if not (initdb and pg_ctl):
        return None
    root = tempfile.mkdtemp(prefix="persona-pg-")
    data, port = os.path.join(root, "data"), _free_port()
    quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "check": True}
    try:
        subprocess.run([initdb, "-D", data, "-A", "trust", "-U", "postgres", "--no-sync"], **quiet)
        subprocess.run(
            [pg_ctl, "-D", data, "-w", "-l", os.path.join(root, "log"),
             "-o", f"-p {port} -k {root} -c listen_addresses=127.0.0.1 -c fsync=off", "start"],
            **quiet,
        )
    except (subprocess.CalledProcessError, OSError):
        shutil.rmtree(root, ignore_errors=True)
        return None

    def stop() -> None:
        subprocess.run([pg_ctl, "-D", data, "-m", "immediate", "stop"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(root, ignore_errors=True)

    atexit.register(stop)
    return f"postgresql://postgres@127.0.0.1:{port}/postgres"
