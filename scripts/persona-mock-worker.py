#!/usr/bin/env python3
"""Deterministic mock Claude worker for harness acceptance tests.

Emits stream-json-like lines, updates worker JSON, optionally touches files
in the worktree, and exits with codes matching real worker semantics.

Modes (PERSONA_MOCK_MODE):
  NORMAL    — emit turns then exit 0 with result num_turns
  NEAR_CAP  — climb to ~82% of budget then idle (supervisor should finish-transition)
  STALL     — emit a few turns then go silent while staying alive (STALE)
  HARD_CAP  — climb to max turns, write result subtype error_max_turns, exit 76
  PARALLEL  — short healthy run (for multi-worker tests)
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


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


def append_stream(stream: Path, obj: dict) -> None:
    stream.parent.mkdir(parents=True, exist_ok=True)
    with stream.open("a") as f:
        f.write(json.dumps(obj) + "\n")
        f.flush()


def update_worker(status_file: Path, **kwargs) -> None:
    data = load_json(status_file)
    data.update(kwargs)
    data["updated_at"] = now()
    write_json(status_file, data)


def touch_activity(worktree: Path) -> None:
    marker = worktree / ".persona-mock-activity"
    marker.write_text(f"touched at {now()}\n")
    # Also bump a tracked-looking file if present
    scratch = worktree / "MOCK_ACTIVITY.txt"
    with scratch.open("a") as f:
        f.write(f"{now()}\n")


def handle_term(signum, frame):  # noqa: ARG001
    raise SystemExit(143)


def main() -> int:
    signal.signal(signal.SIGTERM, handle_term)
    signal.signal(signal.SIGINT, handle_term)

    mode = env("PERSONA_MOCK_MODE", "NORMAL").upper()
    worker_id = env("PERSONA_WORKER_ID", f"mock-{os.getpid()}")
    status_dir = Path(env("PERSONA_WORKER_STATUS_DIR", ".persona-worker"))
    max_turns = int(env("PERSONA_CLAUDE_MAX_TURNS", env("PERSONA_MOCK_MAX_TURNS", "40")))
    worktree = Path(env("PERSONA_WORKER_CWD", os.getcwd()))
    tick = float(env("PERSONA_MOCK_TICK", "0.25"))
    stall_hold = float(env("PERSONA_MOCK_STALL_HOLD", "30"))
    near_idle = float(env("PERSONA_MOCK_NEAR_IDLE", "20"))

    status_file = status_dir / f"{worker_id}.json"
    stream = status_dir / f"{worker_id}.stream.jsonl"
    stderr_log = status_dir / f"{worker_id}.stderr.log"

    # Ensure status exists (launcher may have written it)
    data = load_json(status_file)
    if not data:
        data = {
            "id": worker_id,
            "alias": env("PERSONA_WORKER_ALIAS", "opus"),
            "model": env("PERSONA_WORKER_MODEL", "claude-opus-5-5"),
            "max_turns": max_turns,
            "mode": env("PERSONA_WORKER_MODE", "normal"),
            "state": "RUNNING",
            "cwd": str(worktree),
        }
        write_json(status_file, data)

    update_worker(
        status_file,
        pid=os.getpid(),
        state="RUNNING",
        observed_turns=0,
        observed_turns_source="stream_estimate",
        last_output_at=now(),
        last_meaningful_activity_at=now(),
        stream_log=str(stream),
        stderr_log=str(stderr_log),
        mock_mode=mode,
    )

    append_stream(stream, {"type": "system", "subtype": "init", "mock": True, "mode": mode})
    turns = 0

    def emit_turn(n: int, meaningful: bool = True) -> None:
        nonlocal turns
        turns = n
        append_stream(
            stream,
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": f"mock turn {n}"},
                "mock_turn": n,
            },
        )
        if meaningful:
            touch_activity(worktree)
        update_worker(
            status_file,
            observed_turns=n,
            observed_turns_source="stream_estimate",
            last_output_at=now(),
            last_meaningful_activity_at=now() if meaningful else data.get("last_meaningful_activity_at"),
            budget_pct=int(n * 100 / max_turns) if max_turns else 0,
            state="RUNNING",
        )

    try:
        if mode == "NORMAL":
            target = min(8, max(3, max_turns // 5 or 3))
            for i in range(1, target + 1):
                emit_turn(i, meaningful=True)
                time.sleep(tick)
            append_stream(
                stream,
                {"type": "result", "subtype": "success", "num_turns": target, "is_error": False},
            )
            update_worker(
                status_file,
                observed_turns=target,
                observed_turns_source="result_num_turns",
                state="EXITED_SUCCESS",
                exit_code=0,
                budget_pct=int(target * 100 / max_turns) if max_turns else 0,
            )
            return 0

        if mode == "PARALLEL":
            target = min(5, max(2, max_turns // 8 or 2))
            for i in range(1, target + 1):
                emit_turn(i, meaningful=True)
                time.sleep(tick)
            append_stream(
                stream,
                {"type": "result", "subtype": "success", "num_turns": target, "is_error": False},
            )
            update_worker(
                status_file,
                observed_turns=target,
                observed_turns_source="result_num_turns",
                state="EXITED_SUCCESS",
                exit_code=0,
            )
            return 0

        if mode == "NEAR_CAP":
            # Climb to ~82% then idle so supervisor can FINISH_TRANSITION
            target = max(1, int(max_turns * 0.82))
            for i in range(1, target + 1):
                emit_turn(i, meaningful=(i % 2 == 1))
                time.sleep(tick)
            update_worker(status_file, state="NEAR_FINISH", note="mock near-cap idle")
            # Stay alive idling (no new stream lines) until SIGTERM or timeout
            deadline = now() + near_idle
            while now() < deadline:
                time.sleep(min(tick, 0.5))
            # If never terminated, exit success as if finished
            append_stream(
                stream,
                {"type": "result", "subtype": "success", "num_turns": target, "is_error": False},
            )
            update_worker(
                status_file,
                observed_turns=target,
                observed_turns_source="result_num_turns",
                state="EXITED_SUCCESS",
                exit_code=0,
            )
            return 0

        if mode == "STALL":
            for i in range(1, 3):
                emit_turn(i, meaningful=True)
                time.sleep(tick)
            update_worker(status_file, state="RUNNING", note="mock stalling")
            # Alive but no stream / no git activity
            deadline = now() + stall_hold
            while now() < deadline:
                time.sleep(min(tick, 0.5))
            append_stream(
                stream,
                {"type": "result", "subtype": "success", "num_turns": 2, "is_error": False},
            )
            update_worker(
                status_file,
                observed_turns=2,
                observed_turns_source="result_num_turns",
                state="EXITED_SUCCESS",
                exit_code=0,
            )
            return 0

        if mode == "HARD_CAP":
            for i in range(1, max_turns + 1):
                emit_turn(i, meaningful=(i % 3 == 0))
                time.sleep(tick * 0.5)
            append_stream(
                stream,
                {
                    "type": "result",
                    "subtype": "error_max_turns",
                    "num_turns": max_turns,
                    "is_error": True,
                },
            )
            update_worker(
                status_file,
                observed_turns=max_turns,
                observed_turns_source="result_num_turns",
                state="HARD_CAP",
                exit_code=76,
                note="error_max_turns",
                budget_pct=100,
            )
            return 76

        # Unknown mode — behave like NORMAL short
        emit_turn(1)
        append_stream(stream, {"type": "result", "subtype": "success", "num_turns": 1})
        update_worker(status_file, state="EXITED_SUCCESS", exit_code=0, observed_turns=1)
        return 0

    except SystemExit as e:
        code = int(e.code) if isinstance(e.code, int) else 143
        update_worker(
            status_file,
            state="EXITED_FAILURE" if code not in (0, 143) else "INTERRUPTED",
            exit_code=code,
            observed_turns=turns,
            note=f"terminated signal exit {code}",
        )
        append_stream(stream, {"type": "system", "subtype": "terminated", "exit_code": code})
        return code
    except Exception as e:
        stderr_log.write_text(str(e) + "\n")
        update_worker(status_file, state="EXITED_FAILURE", exit_code=1, note=str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
