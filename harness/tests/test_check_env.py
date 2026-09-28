"""INFRA-002: env contract (scripts/check-env.py) — coverage of code env reads, generated docs
in sync, name-only reporting, exit codes. Stdlib + pytest only (qa:fast)."""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check-env.py"

_spec = importlib.util.spec_from_file_location("check_env", SCRIPT)
ce = importlib.util.module_from_spec(_spec)
sys.modules["check_env"] = ce
_spec.loader.exec_module(ce)

NAME = r"([A-Z][A-Z0-9_]+)"
PY_READS = [
    re.compile(r"(?:environ\.get\(|environ\[|getenv\(|env\.get\(|_flag\(env,\s*)[\"']" + NAME + r"[\"']"),
    # names held in constants, e.g. ENV_KEY = "PERSONA_TOKEN_ENCRYPT_KEY", LIVE_FLAG = "PERSONA_QA_LIVE"
    re.compile(r"^\s*[A-Z_]*(?:ENV|FLAG)[A-Z_]*\s*=\s*[\"']" + NAME + r"[\"']", re.M),
]
PY_TUPLE_LOOP = re.compile(r"for \w+ in \(([^)]*)\):\s*\n\s*if os\.environ")
TS_READS = re.compile(r"process\.env(?:\.|\[[\"'])" + NAME)
GOOD_KEY = "A" * 43 + "="  # urlsafe b64 of 32 bytes (value itself is meaningless)


def _code_env_reads() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in (ROOT / "services/agent/agent").rglob("*.py"):
        text = path.read_text()
        for rx in PY_READS:
            for m in rx.finditer(text):
                found.setdefault(m.group(1), str(path.relative_to(ROOT)))
        for m in PY_TUPLE_LOOP.finditer(text):
            for n in re.findall(r"[\"']" + NAME + r"[\"']", m.group(1)):
                found.setdefault(n, str(path.relative_to(ROOT)))
    web = ROOT / "apps/web"
    for path in web.rglob("*"):
        rel = path.relative_to(web).parts
        if path.suffix not in (".ts", ".tsx", ".mjs", ".js") or not rel or rel[0] in ("node_modules", ".next", "e2e"):
            continue
        for m in TS_READS.finditer(path.read_text()):
            found.setdefault(m.group(1), str(path.relative_to(ROOT)))
    return found


def test_scanner_sees_known_reads():
    found = _code_env_reads()
    for n in ("PERSONA_DATABASE_URL", "PERSONA_TOKEN_ENCRYPT_KEY", "CLOUDFLARE_TURN_KEY_ID",
              "PERSONA_AGENT_BASE_URL", "NEXT_PUBLIC_PERSONA_ICE_URLS", "PERSONA_VOICE_FAKE_VENDORS", "GIT_SHA"):
        assert n in found, n


def test_every_env_read_in_code_is_registered():
    missing = {n: where for n, where in _code_env_reads().items() if n not in ce.BY_NAME}
    assert not missing, f"add to REGISTRY in scripts/check-env.py then --write: {missing}"


def test_registry_names_unique_and_well_formed():
    names = [v.name for v in ce.REGISTRY]
    assert len(names) == len(set(names))
    for v in ce.REGISTRY:
        assert re.fullmatch(NAME, v.name)
        assert set(v.required) <= set(ce.TARGETS)
        assert not (set(v.required) & set(v.forbidden))
        assert v.section in ce.SECTIONS


@pytest.mark.parametrize("kind", sorted(ce.EXAMPLES))
def test_env_examples_are_generated_and_valueless(kind):
    path, _ = ce.EXAMPLES[kind]
    assert path.read_text() == ce.example(kind), f"stale {path}: run python3 scripts/check-env.py --write"
    assert ce.parse_env_file(path) and all(v == "" for v in ce.parse_env_file(path).values())


def test_env_md_is_generated_and_covers_all():
    md = (ROOT / "docs/deploy/ENV.md").read_text()
    assert md == ce.markdown(), "stale docs/deploy/ENV.md: run python3 scripts/check-env.py --write"
    for v in ce.REGISTRY:
        assert f"`{v.name}`" in md


def test_forbidden_test_doubles_not_in_deploy_examples():
    agent = ce.parse_env_file(ROOT / "services/agent/.env.example")
    web = ce.parse_env_file(ROOT / "apps/web/.env.example")
    assert "PERSONA_VOICE_FAKE_VENDORS" not in agent and "GOOGLE_OAUTH_AUTH_URL" not in web
    assert "PERSONA_DATABASE_URL" in agent and "PERSONA_TOKEN_ENCRYPT_KEY" not in web


def _run(*args: str, env_file: Path | None = None) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(SCRIPT), *args] + (["--env-file", str(env_file)] if env_file else [])
    return subprocess.run(argv, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})


def _full(target: str) -> dict[str, str]:
    env = {}
    for v in ce.vars_for(target):
        if target in v.required:
            env[v.name] = {"PERSONA_DATABASE_URL": "postgresql://u:SEKRITpw@h:5432/db",
                           "PERSONA_AGENT_BASE_URL": "https://agent.example",
                           "PERSONA_TOKEN_ENCRYPT_KEY": GOOD_KEY}.get(v.name, f"SEKRIT-{v.name.lower()}")
    return env


def _write(tmp_path: Path, env: dict[str, str]) -> Path:
    p = tmp_path / "t.env"
    p.write_text("# comment\n" + "".join(f"export {k}='{v}'\n" for k, v in env.items()))
    return p


@pytest.mark.parametrize("target", ce.TARGETS)
def test_complete_env_passes_and_never_prints_values(tmp_path, target):
    r = _run("--target", target, env_file=_write(tmp_path, _full(target)))
    assert r.returncode == 0, r.stdout
    assert "RESULT: OK" in r.stdout
    assert "SEKRIT" not in r.stdout + r.stderr and GOOD_KEY not in r.stdout


@pytest.mark.parametrize("target", ce.TARGETS)
def test_missing_required_fails_by_name(tmp_path, target):
    env = _full(target)
    victim = sorted(env)[0]
    del env[victim]
    r = _run("--target", target, env_file=_write(tmp_path, env))
    assert r.returncode == 1
    assert f"MISSING   {victim}" in r.stdout
    assert "SEKRIT" not in r.stdout


def test_allow_missing(tmp_path):
    env = _full("agent")
    del env["CARTESIA_API_KEY"]
    r = _run("--target", "agent", "--allow-missing", "CARTESIA_API_KEY", env_file=_write(tmp_path, env))
    assert r.returncode == 0, r.stdout


def test_invalid_format_and_forbidden(tmp_path):
    env = _full("agent")
    env["PERSONA_TOKEN_ENCRYPT_KEY"] = "SEKRIT-too-short"
    env["PERSONA_VOICE_FAKE_VENDORS"] = "1"
    r = _run("--target", "agent", env_file=_write(tmp_path, env))
    assert r.returncode == 1
    assert "INVALID   PERSONA_TOKEN_ENCRYPT_KEY" in r.stdout
    assert "FORBIDDEN PERSONA_VOICE_FAKE_VENDORS" in r.stdout
    assert "SEKRIT" not in r.stdout


def test_process_env_mode_reads_environ():
    r = _run("--target", "web")  # PATH-only env: everything required is missing
    assert r.returncode == 1 and "MISSING   PERSONA_AGENT_BASE_URL" in r.stdout


def test_generated_example_parses_as_all_missing():
    r = _run("--target", "agent", env_file=ROOT / "services/agent/.env.example")
    assert r.returncode == 1 and "MISSING   ANTHROPIC_API_KEY" in r.stdout


def test_emit_skips_empty_and_forbidden(tmp_path):
    env = _full("web")
    env["GOOGLE_OAUTH_AUTH_URL"] = "http://evil"
    env["UNRELATED"] = "x"
    p = _write(tmp_path, env)
    names = _run("--names", "web", env_file=p).stdout.split()
    assert "GOOGLE_OAUTH_AUTH_URL" not in names and "UNRELATED" not in names
    assert "PERSONA_AGENT_BASE_URL" in names and "GOOGLE_OAUTH_REDIRECT_URL" not in names
    assert "SEKRIT" not in " ".join(names)
    emitted = _run("--emit", "web", env_file=p).stdout
    assert "PERSONA_INTERNAL_SECRET=SEKRIT-persona_internal_secret" in emitted


def test_missing_env_file_is_usage_error(tmp_path):
    assert _run("--target", "agent", env_file=tmp_path / "nope.env").returncode == 2


# --- scripts/db-migrate.sh against local Postgres (skipped when no local server) -------------

def _pg_available() -> bool:
    import shutil
    if not (shutil.which("psql") and shutil.which("createdb")):
        return False
    return subprocess.run(["psql", "-d", "postgres", "-Atc", "select 1"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _pg_available(), reason="no local Postgres (brew services start postgresql)")
def test_db_migrate_idempotent_and_atomic(tmp_path):
    import shutil
    import uuid
    db = f"persona_infra002_t{uuid.uuid4().hex[:8]}"
    url = f"postgresql://localhost/{db}"
    subprocess.run(["createdb", db], check=True)
    try:
        mig = lambda *a, **kw: subprocess.run(["bash", str(ROOT / "scripts/db-migrate.sh"), url, *a],
                                              capture_output=True, text=True, **kw)
        first = mig()
        assert first.returncode == 0, first.stdout + first.stderr
        n = len(list((ROOT / "infra/supabase/migrations").glob("*.sql")))
        assert f"{n} applied, 0 already" in first.stdout
        second = mig()
        assert second.returncode == 0 and f"0 applied, {n} already applied" in second.stdout
        # a failing migration rolls back entirely and is not recorded
        d = tmp_path / "m"
        shutil.copytree(ROOT / "infra/supabase/migrations", d)
        (d / "9999_bad.sql").write_text("create table zz_partial(id int);\nselect 1/0;\n")
        import os
        bad = mig(env={**os.environ, "MIGRATIONS_DIR": str(d)})
        assert bad.returncode != 0 and "FAILED 9999_bad.sql" in bad.stderr
        q = subprocess.run(["psql", "-d", db, "-Atc", "select to_regclass('zz_partial') is null, "
                            "(select count(*) from persona_schema_migrations)"], capture_output=True, text=True)
        assert q.stdout.strip() == f"t|{n}"
        # edited file after apply = drift (reported, exit 1, not re-applied)
        (d / "9999_bad.sql").unlink()
        first_file = sorted(d.glob("*.sql"))[0]
        first_file.write_text(first_file.read_text() + "\n-- edited\n")
        drift = mig(env={**os.environ, "MIGRATIONS_DIR": str(d)})
        assert drift.returncode == 1 and "DRIFT" in drift.stdout
        assert "localhost" in first.stdout and "postgresql://" not in first.stdout
    finally:
        subprocess.run(["dropdb", "--if-exists", "--force", db], capture_output=True)
