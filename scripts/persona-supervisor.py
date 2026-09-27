#!/usr/bin/env python3
"""Persona local Mac worker supervisor daemon.

Manages Claude (or mock) workers under .persona-worker/: heartbeats, stream
parsing, checkpoints, finish transitions, stale detection, caffeinate, locks,
and reconcile after crash/restart.

No distributed platform — boring, inspectable, local only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

VERSION = "1.0.0"
DEFAULT_POLL = 5.0
DEFAULT_STALE = 12 * 60  # 12 minutes
DEFAULT_FINISH_PCT = 82
DEFAULT_MAX_RECOVERY = 2
DEFAULT_FINISH_GIT_IDLE = 90.0  # seconds; stream_estimate finish requires quiet git
CHECKPOINT_PCTS = (40, 70, 85)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def status_dir_from_env() -> Path:
    raw = os.environ.get("PERSONA_WORKER_STATUS_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return (repo_root() / ".persona-worker").resolve()


def env_float(name: str, default: float) -> float:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return float(v)


def env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return int(v)


def now() -> float:
    return time.time()


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text() or "{}")
    except Exception:
        return {}


def append_event(status_dir: Path, etype: str, **fields: Any) -> None:
    events = status_dir / "events.jsonl"
    status_dir.mkdir(parents=True, exist_ok=True)
    evt = {"ts": now(), "type": etype, **fields}
    with events.open("a") as f:
        f.write(json.dumps(evt) + "\n")


def pid_alive(pid: Optional[int]) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def git_status_hash(cwd: str) -> tuple[str, str]:
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except Exception:
        return "", ""
    h = hashlib.sha256(out.encode()).hexdigest()[:16] if out else ""
    summary = "\n".join(out.splitlines()[:40])
    return h, summary


def worktree_mtime(cwd: str) -> float:
    latest = 0.0
    root = Path(cwd)
    if not root.exists():
        return 0.0
    try:
        for p in root.rglob("*"):
            if ".git" in p.parts or ".persona-worker" in p.parts:
                continue
            try:
                latest = max(latest, p.stat().st_mtime)
            except OSError:
                continue
            # Bound scan for large trees
            if latest > 0 and p.is_file():
                # cheap early-exit not required; keep bounded
                pass
    except Exception:
        pass
    return latest


def parse_stream_turns(stream_path: Path, from_offset: int = 0) -> tuple[int, str, int, float]:
    """Return (observed_turns, source, new_offset, last_output_at)."""
    if not stream_path.exists():
        return 0, "none", 0, 0.0
    observed = 0
    source = "none"
    last_out = 0.0
    data = stream_path.read_bytes()
    text = data.decode("utf-8", errors="replace")
    # Prefer full parse for correctness; offset used for activity detection
    lines = text.splitlines()
    final_turns = None
    seen_msg_ids: set[str] = set()
    used_mock = False
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        t = o.get("type")
        # Count unique assistant message ids (one model turn), not every
        # stream-json fragment. Counting user/tool_result rows inflated
        # budget_pct and triggered premature finish_transition kills.
        if t == "assistant" or (
            isinstance(o.get("message"), dict) and o["message"].get("role") == "assistant"
        ):
            msg = o.get("message") if isinstance(o.get("message"), dict) else {}
            mid = msg.get("id")
            if mid:
                if mid not in seen_msg_ids:
                    seen_msg_ids.add(str(mid))
                    observed += 1
            else:
                observed += 1
            if i >= from_offset:
                last_out = max(last_out, stream_path.stat().st_mtime)
        if isinstance(o.get("mock_turn"), int):
            observed = max(observed, int(o["mock_turn"]))
            used_mock = True
            if i >= from_offset:
                last_out = max(last_out, stream_path.stat().st_mtime)
        if t == "result" or o.get("subtype") in ("success", "error_max_turns"):
            if isinstance(o.get("num_turns"), int):
                final_turns = o["num_turns"]
            if i >= from_offset:
                last_out = max(last_out, stream_path.stat().st_mtime)
    if final_turns is not None:
        return final_turns, "result_num_turns", len(lines), last_out
    if used_mock and observed > 0:
        source = "mock_turn"
    elif observed > 0:
        source = "stream_estimate"
    if last_out == 0.0 and stream_path.exists() and len(lines) > from_offset:
        last_out = stream_path.stat().st_mtime
    return observed, source, len(lines), last_out


def lock_path(status_dir: Path, cwd: str) -> Path:
    key = hashlib.sha256(cwd.encode()).hexdigest()[:16]
    return status_dir / "locks" / f"{key}.lock"


def acquire_lock(status_dir: Path, cwd: str, worker_id: str, pid: int) -> bool:
    path = lock_path(status_dir, cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = load_json(path)
        epid = existing.get("pid")
        if epid and pid_alive(int(epid)):
            if existing.get("worker_id") != worker_id:
                return False
    write_json(path, {"pid": pid, "worker_id": worker_id, "cwd": cwd, "at": now()})
    return True


def release_lock(status_dir: Path, cwd: str, worker_id: str) -> None:
    path = lock_path(status_dir, cwd)
    if not path.exists():
        return
    existing = load_json(path)
    if existing.get("worker_id") == worker_id:
        try:
            path.unlink()
        except OSError:
            pass


def worker_is_finish_mode(doc: dict) -> bool:
    """True if this worker is already a finish-mode / finish-id worker."""
    mode = (doc.get("mode") or "").strip().lower()
    if mode == "finish":
        return True
    wid = str(doc.get("id") or "")
    return "-finish-" in wid


def parent_chain_has_finish(doc: dict, workers: Optional[dict] = None) -> bool:
    """True if this worker or any parent_worker_id ancestor is already finish."""
    if worker_is_finish_mode(doc):
        return True
    cur = doc.get("parent_worker_id")
    seen: set[str] = set()
    while cur:
        cur_s = str(cur)
        if cur_s in seen:
            break
        seen.add(cur_s)
        if "-finish-" in cur_s:
            return True
        parent = (workers or {}).get(cur_s)
        if parent is None and workers is not None:
            # Try loading isn't available here; id pattern already checked
            break
        if parent is not None:
            if worker_is_finish_mode(parent):
                return True
            cur = parent.get("parent_worker_id")
            continue
        break
    return False


class Supervisor:
    def __init__(self, status_dir: Path, foreground: bool = False):
        self.status_dir = status_dir
        self.foreground = foreground
        self.poll = env_float("PERSONA_SUPERVISOR_POLL_SECONDS", DEFAULT_POLL)
        self.stale_sec = env_float("PERSONA_STALE_SECONDS", DEFAULT_STALE)
        self.finish_pct = env_int("PERSONA_FINISH_PCT", DEFAULT_FINISH_PCT)
        self.max_recovery = env_int("PERSONA_MAX_RECOVERY", DEFAULT_MAX_RECOVERY)
        self.finish_git_idle = env_float("PERSONA_FINISH_GIT_IDLE_SECONDS", DEFAULT_FINISH_GIT_IDLE)
        self.supervisor_path = status_dir / "supervisor.json"
        self.caffeinate_pid: Optional[int] = None
        self._stream_offsets: dict[str, int] = {}
        self._stop = False
        self.script_dir = Path(__file__).resolve().parent

    def supervisor_doc(self, **extra: Any) -> dict:
        base = load_json(self.supervisor_path)
        active = self.active_worker_ids()
        doc = {
            "pid": os.getpid(),
            "started_at": base.get("started_at", now()),
            "heartbeat_at": now(),
            "hostname": socket.gethostname(),
            "active_worker_ids": active,
            "version": VERSION,
            "caffeinate_pid": self.caffeinate_pid,
            "clean_shutdown": False,
            "status_dir": str(self.status_dir),
            "poll_seconds": self.poll,
            "stale_seconds": self.stale_sec,
        }
        doc.update(extra)
        return doc

    def heartbeat(self, **extra: Any) -> None:
        write_json(self.supervisor_path, self.supervisor_doc(**extra))

    def worker_files(self) -> list[Path]:
        if not self.status_dir.exists():
            return []
        out = []
        for p in self.status_dir.glob("*.json"):
            if p.name in ("supervisor.json",):
                continue
            if p.name.endswith(".tmp"):
                continue
            out.append(p)
        return out

    def load_workers(self) -> dict[str, dict]:
        workers = {}
        for p in self.worker_files():
            doc = load_json(p)
            wid = doc.get("id") or p.stem
            doc["id"] = wid
            doc["_path"] = str(p)
            workers[wid] = doc
        return workers

    def save_worker(self, doc: dict) -> None:
        path = Path(doc.get("_path") or (self.status_dir / f"{doc['id']}.json"))
        clean = {k: v for k, v in doc.items() if not k.startswith("_")}
        clean["updated_at"] = now()
        write_json(path, clean)

    def active_worker_ids(self) -> list[str]:
        ids = []
        for wid, doc in self.load_workers().items():
            state = (doc.get("state") or "").upper()
            pid = doc.get("pid")
            if state in (
                "RUNNING",
                "HEALTHY",
                "NEAR_FINISH",
                "WANDERING",
                "BLOCKED",
                "OVERSIZED",
                "STALE",
                "INTERRUPTED",
            ) and pid_alive(doc.get("pid")):
                ids.append(wid)
            elif state in ("RUNNING", "HEALTHY", "NEAR_FINISH") and pid_alive(pid):
                ids.append(wid)
        # Prefer process-alive definition
        ids = []
        for wid, doc in self.load_workers().items():
            if pid_alive(doc.get("pid")) and (doc.get("state") or "").upper() not in (
                "EXITED_SUCCESS",
                "EXITED_FAILURE",
                "HARD_CAP",
                "RATE_LIMITED",
            ):
                ids.append(wid)
        return sorted(ids)

    def managed_alive(self) -> list[dict]:
        out = []
        for wid, doc in self.load_workers().items():
            if pid_alive(doc.get("pid")):
                st = (doc.get("state") or "").upper()
                if st not in ("EXITED_SUCCESS", "EXITED_FAILURE", "HARD_CAP", "RATE_LIMITED"):
                    out.append(doc)
        return out

    # --- caffeinate ---
    def ensure_caffeinate(self) -> None:
        if self.caffeinate_pid and pid_alive(self.caffeinate_pid):
            return
        # Prefer real caffeinate on macOS; fall back to a noop sleeper marker for tests
        use_mock = os.environ.get("PERSONA_CAFFEINATE_MOCK", "0") == "1"
        try:
            if use_mock:
                proc = subprocess.Popen(
                    [sys.executable, "-c", "import time;\nwhile True: time.sleep(60)"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                proc = subprocess.Popen(
                    ["caffeinate", "-dims"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            self.caffeinate_pid = proc.pid
            append_event(self.status_dir, "SUPERVISOR_CAFFEINATE_START", pid=proc.pid)
        except FileNotFoundError:
            # Non-mac fallback for robustness
            proc = subprocess.Popen(
                [sys.executable, "-c", "import time;\nwhile True: time.sleep(60)"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.caffeinate_pid = proc.pid
            append_event(self.status_dir, "SUPERVISOR_CAFFEINATE_START", pid=proc.pid, mock=True)

    def release_caffeinate(self) -> None:
        if self.caffeinate_pid and pid_alive(self.caffeinate_pid):
            try:
                os.kill(self.caffeinate_pid, signal.SIGTERM)
            except OSError:
                pass
            append_event(self.status_dir, "SUPERVISOR_CAFFEINATE_STOP", pid=self.caffeinate_pid)
        self.caffeinate_pid = None

    def update_caffeinate(self) -> None:
        if self.managed_alive():
            self.ensure_caffeinate()
        else:
            self.release_caffeinate()

    # --- classify / update one worker ---
    def refresh_worker(self, doc: dict) -> dict:
        wid = doc["id"]
        stream = Path(doc.get("stream_log") or (self.status_dir / f"{wid}.stream.jsonl"))
        prev_off = self._stream_offsets.get(wid, 0)
        turns, source, new_off, last_out = parse_stream_turns(stream, prev_off)
        self._stream_offsets[wid] = new_off

        if turns:
            doc["observed_turns"] = turns
            doc["observed_turns_source"] = source
        budget = int(doc.get("max_turns") or 0)
        observed = int(doc.get("observed_turns") or 0)
        pct = int(observed * 100 / budget) if budget else 0
        doc["budget_pct"] = pct

        if last_out:
            doc["last_output_at"] = last_out

        cwd = doc.get("cwd") or doc.get("worktree") or os.getcwd()
        ghash, gsum = git_status_hash(cwd)
        prev_hash = doc.get("git_status_hash") or ""
        if ghash and ghash != prev_hash:
            doc["git_status_hash"] = ghash
            doc["git_dirty_summary"] = gsum
            doc["last_meaningful_activity_at"] = now()
            doc["last_git_activity_at"] = now()
        elif gsum and not doc.get("git_dirty_summary"):
            # First observation of dirty state without hash change edge
            doc["git_dirty_summary"] = gsum
            doc["git_status_hash"] = ghash or doc.get("git_status_hash") or ""
        elif not doc.get("last_meaningful_activity_at"):
            mt = worktree_mtime(cwd)
            if mt:
                doc["last_meaningful_activity_at"] = mt

        alive = pid_alive(doc.get("pid"))
        state = (doc.get("state") or "RUNNING").upper()

        if not alive:
            if state in ("HARD_CAP", "EXITED_SUCCESS", "EXITED_FAILURE", "RATE_LIMITED", "INTERRUPTED"):
                pass
            else:
                code = doc.get("exit_code")
                if code == 76 or state == "HARD_CAP":
                    doc["state"] = "HARD_CAP"
                elif code == 0 or state == "EXITED_SUCCESS":
                    doc["state"] = "EXITED_SUCCESS"
                elif code in (143, 15) or state == "INTERRUPTED":
                    doc["state"] = "INTERRUPTED"
                else:
                    # Prefer existing terminal-ish notes
                    if "HARD_CAP" in str(doc.get("note", "")).upper() or code == 76:
                        doc["state"] = "HARD_CAP"
                    elif code == 0:
                        doc["state"] = "EXITED_SUCCESS"
                    else:
                        doc["state"] = "EXITED_FAILURE"
            self.save_worker(doc)
            return doc

        # Alive — classify
        last_activity = float(doc.get("last_meaningful_activity_at") or doc.get("last_output_at") or doc.get("started_at") or now())
        last_stream = float(doc.get("last_output_at") or 0)
        silent_for = now() - max(last_activity, last_stream)

        # Checkpoints
        cps = doc.get("checkpoints") or {"p40": False, "p70": False, "p85": False}
        for p in CHECKPOINT_PCTS:
            key = f"p{p}"
            if pct >= p and not cps.get(key):
                cps[key] = True
                append_event(self.status_dir, f"CHECKPOINT_{p}", worker_id=wid, budget_pct=pct, observed_turns=observed)
        doc["checkpoints"] = cps

        # Finish-mode workers run to natural exit (0/75/76) unless STALE or HARD_CAP.
        # Never budget-kill them just to launch another finish.
        if worker_is_finish_mode(doc):
            if silent_for >= self.stale_sec:
                new_state = "STALE"
            elif pct >= 95:
                new_state = "OVERSIZED"
            else:
                new_state = "HEALTHY"
        else:
            # Prefer result_num_turns / mock_turn. stream_estimate is approximate
            # even after unique-id counting — require a much higher bar AND a
            # dirty worktree with no recent git activity before auto-terminate.
            source = (doc.get("observed_turns_source") or "")
            finish_bar = self.finish_pct
            can_near_finish = False
            if not doc.get("finish_transitioned"):
                if source == "stream_estimate":
                    finish_bar = max(finish_bar, 98)
                    dirty = bool((doc.get("git_dirty_summary") or "").strip())
                    last_git = float(
                        doc.get("last_git_activity_at")
                        or doc.get("last_meaningful_activity_at")
                        or 0
                    )
                    git_idle = (now() - last_git) if last_git else 1e9
                    can_near_finish = (
                        pct >= finish_bar
                        and dirty
                        and git_idle >= self.finish_git_idle
                    )
                else:
                    can_near_finish = pct >= finish_bar
            if can_near_finish:
                new_state = "NEAR_FINISH"
            elif silent_for >= self.stale_sec:
                new_state = "STALE"
            elif pct >= 95:
                new_state = "OVERSIZED"
            else:
                new_state = "HEALTHY"

        doc["state"] = new_state
        self.save_worker(doc)
        return doc

    def terminate_worker(self, doc: dict, reason: str) -> None:
        pid = doc.get("pid")
        wid = doc["id"]
        if pid and pid_alive(pid):
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass
            # wait up to ~8s
            for _ in range(16):
                if not pid_alive(pid):
                    break
                time.sleep(0.5)
            if pid_alive(pid):
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except OSError:
                    pass
        doc["note"] = reason
        doc["state"] = "INTERRUPTED"
        doc["exit_code"] = 143
        self.save_worker(doc)
        release_lock(self.status_dir, doc.get("cwd") or "", wid)
        append_event(self.status_dir, "WORKER_TERMINATED", worker_id=wid, reason=reason)

    def write_finish_packet(self, doc: dict) -> Path:
        packets = self.status_dir / "packets"
        packets.mkdir(parents=True, exist_ok=True)
        path = packets / f"{doc['id']}-finish.md"
        original = doc.get("packet") or ""
        body = f"""TASK: Finish-only continuation for worker {doc['id']}
WHY: Near budget / finish transition — close out acceptance without rediscovery
SCOPE: Existing dirty work in {doc.get('cwd')} only
REQUIREMENTS: Inspect diff; preserve working implementation; no unrelated refactors
ACCEPTANCE: Run targeted acceptance from original packet; commit; concise report
CONSTRAINTS: FINISH MODE — no broad exploration

Original packet path: {original}

Observed turns: {doc.get('observed_turns')} / {doc.get('max_turns')}
Git dirty summary:
{doc.get('git_dirty_summary') or '(none recorded)'}
"""
        path.write_text(body)
        return path

    def launch_finish_continuation(self, doc: dict, *, reason: str = "checkpoint") -> Optional[str]:
        recovery = int(doc.get("recovery_count") or 0)
        if recovery >= self.max_recovery:
            append_event(self.status_dir, "RECOVERY_SKIPPED", worker_id=doc["id"], reason="max_recovery")
            return None
        # Cap nested finish: if parent chain already includes finish, only HARD_CAP
        # (exit 76) with dirty worktree may launch another finish — not CHECKPOINT_FINISH.
        workers = self.load_workers()
        if parent_chain_has_finish(doc, workers) and reason != "hard_cap":
            append_event(
                self.status_dir,
                "RECOVERY_SKIPPED",
                worker_id=doc["id"],
                reason="nested_finish_blocked",
                via=reason,
            )
            return None
        if reason == "hard_cap":
            dirty = (doc.get("git_dirty_summary") or "").strip()
            if not dirty:
                append_event(
                    self.status_dir,
                    "RECOVERY_SKIPPED",
                    worker_id=doc["id"],
                    reason="hard_cap_clean_tree",
                )
                return None
        cwd = doc.get("cwd") or os.getcwd()
        packet = self.write_finish_packet(doc)
        new_id = f"{doc['id']}-finish-{int(now())}"
        if not acquire_lock(self.status_dir, cwd, new_id, os.getpid()):
            append_event(self.status_dir, "RECOVERY_SKIPPED", worker_id=doc["id"], reason="lock_held")
            return None

        env = os.environ.copy()
        env["PERSONA_WORKER_MODE"] = "finish"
        env["PERSONA_WORKER_ID"] = new_id
        env["PERSONA_WORKER_STATUS_DIR"] = str(self.status_dir)
        env["PERSONA_PARENT_WORKER_ID"] = doc["id"]
        env["PERSONA_RECOVERY_COUNT"] = str(recovery + 1)
        env["PERSONA_FORCE_LOCK"] = "1"
        if doc.get("max_turns"):
            # shorter finish budget: half, min 8
            env["PERSONA_CLAUDE_MAX_TURNS"] = str(max(8, int(doc["max_turns"]) // 2))
        # Preserve mock mode if parent was mock / tests
        if os.environ.get("PERSONA_WORKER_MOCK") == "1" or doc.get("mock_mode"):
            env["PERSONA_WORKER_MOCK"] = "1"
            env["PERSONA_MOCK_MODE"] = os.environ.get("PERSONA_FINISH_MOCK_MODE", "NORMAL")

        alias = doc.get("alias") or "opus"
        cmd = [
            str(self.script_dir / "claude-worker.sh"),
            alias,
            "--packet",
            str(packet),
            "--worker-id",
            new_id,
        ]
        append_event(
            self.status_dir,
            "FINISH_TRANSITION",
            worker_id=doc["id"],
            new_worker_id=new_id,
            packet=str(packet),
        )
        append_event(self.status_dir, "RECOVERY_LAUNCH", worker_id=doc["id"], new_worker_id=new_id)
        subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        doc["finish_transitioned"] = True
        doc["recovery_count"] = recovery + 1
        self.save_worker(doc)
        return new_id

    def handle_stale(self, doc: dict) -> None:
        wid = doc["id"]
        append_event(self.status_dir, "WORKER_STALE", worker_id=wid)
        # Re-check after a short inspect window
        time.sleep(min(2.0, self.poll))
        doc = self.refresh_worker(load_json(Path(doc["_path"])) if doc.get("_path") else doc)
        # reload
        workers = self.load_workers()
        doc = workers.get(wid, doc)
        last_activity = float(doc.get("last_meaningful_activity_at") or doc.get("last_output_at") or 0)
        if now() - last_activity < self.stale_sec:
            doc["state"] = "HEALTHY"
            self.save_worker(doc)
            return
        dirty = (doc.get("git_dirty_summary") or "").strip()
        self.terminate_worker(doc, "stale_timeout")
        if dirty and int(doc.get("recovery_count") or 0) < self.max_recovery:
            # reload after terminate
            doc = self.load_workers().get(wid, doc)
            doc["state"] = "STALE"
            self.save_worker(doc)
            self.launch_finish_continuation(doc, reason="stale")
        else:
            doc = self.load_workers().get(wid, doc)
            doc["state"] = "BLOCKED"
            doc["note"] = "stale_no_useful_recovery"
            self.save_worker(doc)
            append_event(self.status_dir, "WORKER_BLOCKED", worker_id=wid, reason="stale")

    def handle_near_finish(self, doc: dict) -> None:
        if doc.get("finish_transitioned"):
            return
        wid = doc["id"]
        # Never finish-transition / SIGTERM a worker already in finish mode.
        if worker_is_finish_mode(doc) or parent_chain_has_finish(doc, self.load_workers()):
            append_event(
                self.status_dir,
                "FINISH_TRANSITION_SKIPPED",
                worker_id=wid,
                reason="already_finish_mode",
                budget_pct=doc.get("budget_pct"),
            )
            return
        append_event(self.status_dir, "CHECKPOINT_FINISH", worker_id=wid, budget_pct=doc.get("budget_pct"))
        self.terminate_worker(doc, "finish_transition")
        doc = self.load_workers().get(wid, doc)
        doc["state"] = "NEAR_FINISH"
        self.save_worker(doc)
        self.launch_finish_continuation(doc, reason="checkpoint")

    def handle_hard_cap(self, doc: dict) -> None:
        if doc.get("hard_cap_handled"):
            return
        wid = doc["id"]
        append_event(self.status_dir, "WORKER_HARD_CAP", worker_id=wid)
        dirty = (doc.get("git_dirty_summary") or "").strip()
        # Never reset worktree. Nested finish launches only on HARD_CAP + dirty.
        recovery = int(doc.get("recovery_count") or 0)
        already_finished = bool(doc.get("finish_transitioned"))
        in_finish = worker_is_finish_mode(doc) or parent_chain_has_finish(doc, self.load_workers())
        launched = None
        if dirty and recovery < self.max_recovery:
            if already_finished and not in_finish:
                # Non-finish parent already handed off once — preserve.
                pass
            else:
                launched = self.launch_finish_continuation(doc, reason="hard_cap")
        if not launched:
            append_event(self.status_dir, "QA_READY", worker_id=wid, reason="hard_cap_preserve")
        doc = self.load_workers().get(wid, doc)
        doc["hard_cap_handled"] = True
        doc["state"] = "HARD_CAP"
        self.save_worker(doc)

    def tick(self) -> None:
        workers = self.load_workers()
        for wid, doc in list(workers.items()):
            doc = self.refresh_worker(doc)
            state = (doc.get("state") or "").upper()
            if (
                state == "NEAR_FINISH"
                and pid_alive(doc.get("pid"))
                and not doc.get("finish_transitioned")
                and not worker_is_finish_mode(doc)
            ):
                self.handle_near_finish(doc)
            elif state == "STALE" and pid_alive(doc.get("pid")):
                self.handle_stale(doc)
            elif state == "HARD_CAP":
                self.handle_hard_cap(doc)
            elif state in ("EXITED_SUCCESS",) and not doc.get("exit_evented"):
                append_event(self.status_dir, "WORKER_EXITED", worker_id=wid, state=state, exit_code=doc.get("exit_code"))
                doc["exit_evented"] = True
                self.save_worker(doc)

        self.update_caffeinate()
        self.heartbeat()

    def reconcile(self) -> dict:
        """Scan state/PIDs/worktrees; fix stale records; no duplicate launches."""
        report = {"interrupted": [], "active": [], "terminal": [], "locks_cleared": []}
        workers = self.load_workers()
        for wid, doc in workers.items():
            pid = doc.get("pid")
            state = (doc.get("state") or "").upper()
            alive = pid_alive(pid)
            if alive:
                report["active"].append(wid)
                continue
            if state in ("RUNNING", "HEALTHY", "NEAR_FINISH", "WANDERING", "STALE", "OVERSIZED"):
                doc["state"] = "INTERRUPTED"
                doc["note"] = "reconcile_dead_pid"
                self.save_worker(doc)
                report["interrupted"].append(wid)
                append_event(self.status_dir, "MACHINE_RECONCILED", worker_id=wid, action="mark_INTERRUPTED")
                cwd = doc.get("cwd") or ""
                release_lock(self.status_dir, cwd, wid)
                report["locks_cleared"].append(wid)
            else:
                report["terminal"].append(wid)
                cwd = doc.get("cwd") or ""
                # clear locks for dead workers
                lp = lock_path(self.status_dir, cwd) if cwd else None
                if lp and lp.exists():
                    existing = load_json(lp)
                    if existing.get("worker_id") == wid or not pid_alive(existing.get("pid")):
                        try:
                            lp.unlink()
                            report["locks_cleared"].append(wid)
                        except OSError:
                            pass

        # Clear orphan locks
        locks_dir = self.status_dir / "locks"
        if locks_dir.exists():
            for lp in locks_dir.glob("*.lock"):
                existing = load_json(lp)
                if not pid_alive(existing.get("pid")):
                    try:
                        lp.unlink()
                    except OSError:
                        pass

        # Supervisor heartbeat file may be stale — caller may rewrite
        append_event(self.status_dir, "SUPERVISOR_RECONCILE", report=report)
        return report

    def watch_loop(self) -> None:
        self.status_dir.mkdir(parents=True, exist_ok=True)
        (self.status_dir / "locks").mkdir(parents=True, exist_ok=True)
        if not self.supervisor_path.exists() or load_json(self.supervisor_path).get("clean_shutdown"):
            write_json(
                self.supervisor_path,
                {
                    "pid": os.getpid(),
                    "started_at": now(),
                    "heartbeat_at": now(),
                    "hostname": socket.gethostname(),
                    "active_worker_ids": [],
                    "version": VERSION,
                    "caffeinate_pid": None,
                    "clean_shutdown": False,
                    "status_dir": str(self.status_dir),
                },
            )
        else:
            # restart — keep started_at if present
            doc = load_json(self.supervisor_path)
            unclean = not doc.get("clean_shutdown", True)
            doc["pid"] = os.getpid()
            doc["clean_shutdown"] = False
            doc["heartbeat_at"] = now()
            write_json(self.supervisor_path, doc)
            if unclean:
                append_event(self.status_dir, "SUPERVISOR_RECOVERED", pid=os.getpid(), version=VERSION)

        append_event(self.status_dir, "SUPERVISOR_STARTED", pid=os.getpid(), version=VERSION)
        self.reconcile()

        def _stop_handler(signum, frame):  # noqa: ARG001
            self._stop = True

        signal.signal(signal.SIGTERM, _stop_handler)
        signal.signal(signal.SIGINT, _stop_handler)

        while not self._stop:
            try:
                self.tick()
            except Exception as e:
                append_event(self.status_dir, "SUPERVISOR_ERROR", error=str(e))
            # sleep in slices for faster shutdown
            end = now() + self.poll
            while not self._stop and now() < end:
                time.sleep(0.2)

        self.shutdown()

    def shutdown(self) -> None:
        self.release_caffeinate()
        doc = self.supervisor_doc(clean_shutdown=True)
        doc["clean_shutdown"] = True
        doc["stopped_at"] = now()
        write_json(self.supervisor_path, doc)
        append_event(self.status_dir, "SUPERVISOR_STOPPED", pid=os.getpid())


def cmd_start(args: argparse.Namespace) -> int:
    status_dir = Path(args.status_dir).resolve() if args.status_dir else status_dir_from_env()
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / "locks").mkdir(parents=True, exist_ok=True)

    if not args.foreground:
        # Daemonize simply via double-fork-ish subprocess detachment
        log = status_dir / "supervisor.daemon.log"
        env = os.environ.copy()
        env["PERSONA_WORKER_STATUS_DIR"] = str(status_dir)
        cmd = [sys.executable, str(Path(__file__).resolve()), "watch", "--status-dir", str(status_dir)]
        # Forward useful test/config env
        for k, v in os.environ.items():
            if k.startswith("PERSONA_"):
                env[k] = v
        proc = subprocess.Popen(
            cmd,
            cwd=str(repo_root()),
            env=env,
            stdout=open(log, "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        # Wait briefly for supervisor.json
        for _ in range(50):
            doc = load_json(status_dir / "supervisor.json")
            if doc.get("pid") and pid_alive(doc["pid"]):
                print(f"supervisor started pid={doc['pid']} status_dir={status_dir}")
                return 0
            time.sleep(0.1)
        print(f"supervisor launch requested pid={proc.pid} (waiting for heartbeat)", file=sys.stderr)
        return 0

    sup = Supervisor(status_dir, foreground=True)
    sup.watch_loop()
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    status_dir = Path(args.status_dir).resolve() if args.status_dir else status_dir_from_env()
    doc = load_json(status_dir / "supervisor.json")
    pid = doc.get("pid")
    if pid and pid_alive(pid):
        os.kill(int(pid), signal.SIGTERM)
        for _ in range(50):
            if not pid_alive(pid):
                break
            time.sleep(0.1)
        if pid_alive(pid):
            os.kill(int(pid), signal.SIGKILL)
        print(f"stopped supervisor pid={pid}")
    else:
        print("no live supervisor")
    # Ensure caffeinate released
    cpid = doc.get("caffeinate_pid")
    if cpid and pid_alive(cpid):
        try:
            os.kill(int(cpid), signal.SIGTERM)
        except OSError:
            pass
    if doc:
        doc["clean_shutdown"] = True
        doc["caffeinate_pid"] = None
        doc["heartbeat_at"] = now()
        write_json(status_dir / "supervisor.json", doc)
        append_event(status_dir, "SUPERVISOR_STOPPED", pid=pid, via="stop_cmd")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    status_dir = Path(args.status_dir).resolve() if args.status_dir else status_dir_from_env()
    doc = load_json(status_dir / "supervisor.json")
    workers = {}
    for p in status_dir.glob("*.json"):
        if p.name == "supervisor.json":
            continue
        w = load_json(p)
        workers[w.get("id") or p.stem] = w
    print(json.dumps({"supervisor": doc, "workers": workers}, indent=2))
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    status_dir = Path(args.status_dir).resolve() if args.status_dir else status_dir_from_env()
    sup = Supervisor(status_dir)
    report = sup.reconcile()
    print(json.dumps(report, indent=2))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    status_dir = Path(args.status_dir).resolve() if args.status_dir else status_dir_from_env()
    sup = Supervisor(status_dir, foreground=True)
    # If another live supervisor owns the file, refuse duplicate (unless forced)
    existing = load_json(status_dir / "supervisor.json")
    if (
        existing.get("pid")
        and pid_alive(existing["pid"])
        and int(existing["pid"]) != os.getpid()
        and not existing.get("clean_shutdown")
        and os.environ.get("PERSONA_SUPERVISOR_FORCE", "0") != "1"
    ):
        # Allow takeover if heartbeat is very stale (machine restart simulation)
        hb = float(existing.get("heartbeat_at") or 0)
        if now() - hb < env_float("PERSONA_SUPERVISOR_STALE_HEARTBEAT", 60):
            print(f"supervisor already running pid={existing['pid']}", file=sys.stderr)
            return 1
    sup.watch_loop()
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    status_dir = Path(args.status_dir).resolve() if args.status_dir else status_dir_from_env()
    wid = args.worker_id
    append_event(status_dir, "WORKER_STARTED", worker_id=wid)
    # Touch supervisor heartbeat if present (do not require supervisor)
    path = status_dir / "supervisor.json"
    if path.exists():
        doc = load_json(path)
        ids = list(doc.get("active_worker_ids") or [])
        if wid not in ids:
            ids.append(wid)
        doc["active_worker_ids"] = ids
        doc["heartbeat_at"] = now()
        write_json(path, doc)
    print(f"registered {wid}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Persona local worker supervisor")
    parser.add_argument("--status-dir", default=os.environ.get("PERSONA_WORKER_STATUS_DIR"))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start")
    p_start.add_argument("--foreground", action="store_true")
    p_start.add_argument("--status-dir", default=None)

    p_stop = sub.add_parser("stop")
    p_stop.add_argument("--status-dir", default=None)

    p_status = sub.add_parser("status")
    p_status.add_argument("--status-dir", default=None)

    p_rec = sub.add_parser("reconcile")
    p_rec.add_argument("--status-dir", default=None)

    p_watch = sub.add_parser("watch")
    p_watch.add_argument("--status-dir", default=None)

    p_reg = sub.add_parser("register")
    p_reg.add_argument("worker_id")
    p_reg.add_argument("--status-dir", default=None)

    args = parser.parse_args()
    # Normalize status_dir: subparser may override
    if getattr(args, "status_dir", None):
        os.environ["PERSONA_WORKER_STATUS_DIR"] = args.status_dir

    if args.cmd == "start":
        return cmd_start(args)
    if args.cmd == "stop":
        return cmd_stop(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "reconcile":
        return cmd_reconcile(args)
    if args.cmd == "watch":
        return cmd_watch(args)
    if args.cmd == "register":
        return cmd_register(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
