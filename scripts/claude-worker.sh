#!/usr/bin/env bash
# Persona onboarding Claude worker — B-high (opus) / C (fable) only.
# Ported from FLOAT's scripts/claude-worker.sh (Darran's own EM harness).
# Roles: impl (default), design (DESIGN worker), frontend (FRONTEND IMPLEMENTER).
# Writes per-worker state under .persona-worker/ and streams via stream-json.
# Local supervisor owns lifecycle; EM reconciles events (see AGENTS.md +
# docs/orchestration/LOCAL-SUPERVISOR.md).
set -euo pipefail

usage() {
  cat <<'USAGE' >&2
Usage: ./scripts/claude-worker.sh [opus|fable] [options] ["task"|--packet FILE]

  opus   — Claude Opus 5.5 (B-high default worker). Model id from
           PERSONA_OPUS_MODEL (default: claude-opus-5-5)
  fable  — Claude Fable 5 (C escalation only). PERSONA_FABLE_MODEL
           (default: claude-fable-5)

Options:
  --role ROLE       impl (default) | design | frontend  (see AGENTS.md "Worker roles")
  --packet FILE     Bounded implementation packet (preferred)
  --probe-model     Spend ONE tiny real Claude call to confirm the CLI accepts
                    the resolved model id, print the served model, and exit
  --max-turns N     Override turn budget (default: opus 40, fable 60)
  --dry-run         Print prompt metadata and exit (no Claude call)
  --worker-id ID    Explicit worker id (default: auto)
  --milestone NAME  Optional milestone tag for status
  --detach          Run in a new session (setsid, SIGHUP-immune), print WORKER_STATUS and
                    return at once; the worker outlives the launching shell/routine run.
                    DEFAULT for real runs without a TTY (routines, agent tool shells);
                    --foreground (or PERSONA_WORKER_DETACH=0) keeps the old blocking run.

Env:
  PERSONA_WORKER_PACKET / PERSONA_WORKER_PACKET path via --packet
  PERSONA_OPUS_MODEL / PERSONA_FABLE_MODEL / PERSONA_WORKER_ROLE
  PERSONA_WORKER_MAX_TURNS / PERSONA_CLAUDE_MAX_TURNS
  PERSONA_WORKER_ID / PERSONA_WORKER_STATUS_DIR / PERSONA_WORKER_MODE=finish
  PERSONA_WORKER_SKIP_PREFLIGHT_CHECK=1
  PERSONA_WORKER_MOCK=1 / PERSONA_MOCK_MODE=NORMAL|NEAR_CAP|STALL|HARD_CAP|PARALLEL
  PERSONA_RECOVERY_COUNT / PERSONA_PARENT_WORKER_ID / PERSONA_FORCE_LOCK=1

Exit codes:
  0   success
  75  rate limited — defer B-high/C; never downgrade to Grok Build
  76  hard turn cap — preserve worktree; never reset
USAGE
  exit 1
}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ORIG_ARGS=("$@")
DETACH="${PERSONA_WORKER_DETACH:-auto}"
# Claude CLI binary: prefer the user's native install (~/.local/bin/claude, auto-updates
# without sudo) over a stale root-owned npm-global /usr/local/bin/claude.
if [ -z "${PERSONA_CLAUDE_BIN:-}" ]; then
  if [ -x "$HOME/.local/bin/claude" ]; then
    PERSONA_CLAUDE_BIN="$HOME/.local/bin/claude"
  else
    PERSONA_CLAUDE_BIN="claude"
  fi
fi
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

MODEL_ALIAS="opus"
ROLE="${PERSONA_WORKER_ROLE:-impl}"
PROBE=0
PACKET="${PERSONA_WORKER_PACKET:-}"
MAX_TURNS=""
DRY_RUN=0
WORKER_ID="${PERSONA_WORKER_ID:-}"
MILESTONE="${PERSONA_WORKER_MILESTONE:-}"
TASK_ARGS=()

if [ "$#" -eq 0 ] && [ -z "$PACKET" ]; then
  usage
fi
# (a bare `opus --probe-model` is allowed; handled after model resolution)

# Allow env-only invocation when packet is set
if [ "$#" -gt 0 ]; then
  case "$1" in
    opus|fable)
      MODEL_ALIAS="$1"
      shift
      ;;
  esac
fi

while [ "$#" -gt 0 ]; do
  case "$1" in
    --packet)
      [ "$#" -ge 2 ] || usage
      PACKET="$2"
      shift 2
      ;;
    --max-turns)
      [ "$#" -ge 2 ] || usage
      MAX_TURNS="$2"
      shift 2
      ;;
    --worker-id)
      [ "$#" -ge 2 ] || usage
      WORKER_ID="$2"
      shift 2
      ;;
    --milestone)
      [ "$#" -ge 2 ] || usage
      MILESTONE="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --role)
      [ "$#" -ge 2 ] || usage
      ROLE="$2"
      shift 2
      ;;
    --probe-model)
      PROBE=1
      shift
      ;;
    --detach)
      DETACH=1
      shift
      ;;
    --foreground)
      DETACH=0
      shift
      ;;
    -h|--help)
      usage
      ;;
    --)
      shift
      while [ "$#" -gt 0 ]; do TASK_ARGS+=("$1"); shift; done
      break
      ;;
    *)
      TASK_ARGS+=("$1")
      shift
      ;;
  esac
done

# WRK-001: workers launched from a routine or an agent's tool shell died with that run
# (LAT-002 x2, Mon 01:17/01:19: stream cut mid-turn, empty stderr, no exit status written):
# the wrapper and Claude were in the launcher's process group/session and got its
# SIGHUP/kill. Detach = re-exec this script in a NEW SESSION (os.setsid after a fork, so
# it is never the group leader) with SIGHUP ignored and stdio off the launcher, then
# report the status path and return. Mock/test, dry-run and probe runs stay foreground.
if [ "$DETACH" = "auto" ]; then
  if [ -t 0 ] || [ "${PERSONA_WORKER_MOCK:-0}" = "1" ] || [ "$DRY_RUN" = "1" ] || [ "$PROBE" = "1" ]; then
    DETACH=0
  else
    DETACH=1
  fi
fi
if [ "$DETACH" = "1" ] && [ -z "${PERSONA_WORKER_DETACHED:-}" ]; then
  _SDIR="${PERSONA_WORKER_STATUS_DIR:-$REPO_ROOT/.persona-worker}"
  mkdir -p "$_SDIR"
  LAUNCH_LOG="$_SDIR/launch-$(date +%Y%m%d-%H%M%S)-$$.log"
  PY_BIN="$(command -v python3)"
  PERSONA_WORKER_DETACHED=1 "$PY_BIN" - "$LAUNCH_LOG" "$0" "${ORIG_ARGS[@]}" <<'PYDETACH'
import os, signal, sys
log, argv = sys.argv[1], sys.argv[2:]
if os.fork():                 # parent: back to the launcher
    os._exit(0)
os.setsid()                   # child: new session + process group, no controlling TTY
signal.signal(signal.SIGHUP, signal.SIG_IGN)
if os.fork():                 # never reacquire a TTY; reparented to launchd/init
    os._exit(0)
fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
nul = os.open(os.devnull, os.O_RDONLY)
os.dup2(nul, 0); os.dup2(fd, 1); os.dup2(fd, 2)
os.execv("/bin/bash", ["/bin/bash", *argv])
PYDETACH
  # Wait (<=20s) for the detached worker to register, then hand its status path back.
  for _ in $(seq 1 40); do
    if grep -q "^WORKER_STATUS " "$LAUNCH_LOG" 2>/dev/null; then break; fi
    sleep 0.5
  done
  grep "^WORKER_STATUS " "$LAUNCH_LOG" 2>/dev/null | head -1 || true
  echo "persona-worker: detached (new session); launch log $LAUNCH_LOG"
  exit 0
fi

case "$MODEL_ALIAS" in
  # Opus 5.5 = claude-opus-5-5 per platform.claude.com model docs (released
  # 2026-09-22). FLOAT/LMPROJ still pin claude-opus-5 (verified served in
  # their stream logs). Run `--probe-model` once to confirm the CLI accepts 5.5.
  opus)  MODEL="${PERSONA_OPUS_MODEL:-claude-opus-5-5}"; DEFAULT_TURNS=40; CLASS="B-high" ;;
  fable) MODEL="${PERSONA_FABLE_MODEL:-claude-fable-5}"; DEFAULT_TURNS=60; CLASS="C" ;;
  *)
    echo "Unknown model alias: $MODEL_ALIAS" >&2
    usage
    ;;
esac

case "$ROLE" in
  impl|design|frontend) ;;
  *) echo "Unknown role: $ROLE (impl|design|frontend)" >&2; usage ;;
esac

if [ "$PROBE" -eq 1 ]; then
  echo "probe: $PERSONA_CLAUDE_BIN ($("$PERSONA_CLAUDE_BIN" --version 2>/dev/null)) -p (1 turn) --model $MODEL" >&2
  "$PERSONA_CLAUDE_BIN" -p "Reply with exactly: OK" --model "$MODEL" --max-turns 1 \
    --output-format stream-json --verbose </dev/null 2>&1 \
    | python3 -c 'import sys,json
seen=set()
for l in sys.stdin:
    try: o=json.loads(l)
    except Exception:
        print("raw:", l.strip()[:300]); continue
    m=o.get("model") or (o.get("message") or {}).get("model")
    if m and m not in seen: seen.add(m); print("served_model:", m)
    if o.get("type")=="result": print("result:", o.get("subtype"), "is_error:", o.get("is_error"))'
  exit $?
fi

# Compat: PERSONA_WORKER_MAX_TURNS (legacy EM) and PERSONA_CLAUDE_MAX_TURNS
if [ -z "$MAX_TURNS" ]; then
  if [ -n "${PERSONA_WORKER_MAX_TURNS:-}" ]; then
    MAX_TURNS="$PERSONA_WORKER_MAX_TURNS"
  elif [ -n "${PERSONA_CLAUDE_MAX_TURNS:-}" ]; then
    MAX_TURNS="$PERSONA_CLAUDE_MAX_TURNS"
  fi
fi
MAX_TURNS="${MAX_TURNS:-$DEFAULT_TURNS}"

STATUS_DIR="${PERSONA_WORKER_STATUS_DIR:-$REPO_ROOT/.persona-worker}"
mkdir -p "$STATUS_DIR/locks"
export PERSONA_WORKER_STATUS_DIR="$STATUS_DIR"

if [ -z "$WORKER_ID" ]; then
  WORKER_ID="${MODEL_ALIAS}-$(date +%s)-$$"
fi
export PERSONA_WORKER_ID="$WORKER_ID"

STATUS_FILE="$STATUS_DIR/${WORKER_ID}.json"
STREAM_LOG="$STATUS_DIR/${WORKER_ID}.stream.jsonl"
STDERR_LOG="$STATUS_DIR/${WORKER_ID}.stderr.log"
: >"$STREAM_LOG"
: >"$STDERR_LOG"

TASK_BODY=""
if [ -n "$PACKET" ]; then
  if [ ! -f "$PACKET" ]; then
    echo "Packet not found: $PACKET" >&2
    exit 1
  fi
  TASK_BODY=$(cat "$PACKET")
elif [ "${#TASK_ARGS[@]}" -gt 0 ]; then
  TASK_BODY="${TASK_ARGS[*]}"
else
  echo "Provide a task string or --packet FILE" >&2
  usage
fi

WORKER_MODE="${PERSONA_WORKER_MODE:-normal}"
if [ "$WORKER_MODE" = "finish" ]; then
  TASK_BODY="FINISH MODE (EM continuation - STOP BROAD EXPLORATION):
Inspect current diff first. Preserve working implementation. No unrelated refactors.
Resolve only acceptance-blocking issues. Run targeted acceptance tests from the packet.
Fix only failures required for the task. Commit. Return concise commit/files/tests/risks.
Do not rediscover the project, optional polish, or broad QA layers.

${TASK_BODY}"
fi

# Packet preflight (Persona semantics) — warn unless skipped
if [ "${PERSONA_WORKER_SKIP_PREFLIGHT_CHECK:-0}" != "1" ]; then
  missing=0
  REQUIRED_KEYS="TASK WHY SCOPE REQUIREMENTS ACCEPTANCE"
  case "$ROLE" in
    design)   REQUIRED_KEYS="$REQUIRED_KEYS REFERENCES DELIVERABLES" ;;
    frontend) REQUIRED_KEYS="$REQUIRED_KEYS SPEC VISUAL_ACCEPTANCE" ;;
  esac
  for key in $REQUIRED_KEYS; do
    if ! printf "%s" "$TASK_BODY" | grep -qiE "(^|[[:space:]])${key}([[:space:]]|:|-|$)"; then
      missing=1
      break
    fi
  done
  if [ "$missing" -eq 1 ]; then
    echo "persona-worker: warning - incomplete packet for role $ROLE (need: $REQUIRED_KEYS)" >&2
  fi
fi

WORKDIR="$(pwd)"
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
HEAD_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
START_EPOCH="$(date +%s)"

case "$ROLE" in
  design)
    ROLE_BLOCK="ROLE: DESIGN worker. You research and specify; you do NOT write production code.
Use the repo skills in .claude/skills (frontend-design first) and follow docs/design/README.md.
Capture reference screenshots with Playwright (harness/visual/capture.mjs) into docs/design/references/.
Deliver a written spec + static reference mockups (HTML/CSS or PNG) under docs/design/ only.
Never copy third-party brand assets verbatim into apps/; references are for analysis only." ;;
  frontend)
    ROLE_BLOCK="ROLE: FRONTEND IMPLEMENTER. Implement the referenced design spec in apps/web only.
Use .claude/skills/frontend-design and .claude/skills/webapp-testing. Verify with Playwright flow tests
(apps/web/e2e) and screenshot diffs against the spec mockups (npm run qa:visual). Report diff scores." ;;
  *)
    ROLE_BLOCK="ROLE: implementation worker." ;;
esac

PROMPT=$(cat <<EOF
You are a Persona-onboarding implementation worker under the Grok Engineering Manager.
You are NOT the project manager.

Worker class: ${CLASS} | alias: ${MODEL_ALIAS} | model: ${MODEL} | role: ${ROLE} | max-turns: ${MAX_TURNS}
Worker id: ${WORKER_ID}
Mode: ${WORKER_MODE}
${ROLE_BLOCK}

Do NOT rediscover the whole project. Read ONLY the packet READ list
(plus CLAUDE.md and docs/ARCHITECTURE.md if not already listed).
Honor DO NOT READ. Stay inside SCOPE. Never read .env files or secrets.
Penciled voice-agent code is owned by Darran (his IP): you MAY read and copy it into this repo,
from the sanitized read-only mirror /Users/darranshivdat/IdeaProjects/persona-onboarding-ref/penciled-voice-agent/
(see its PROVENANCE.md). NEVER modify anything in penciled-emr or the mirror.
NEVER read or copy .env*, transcripts/, data/, demo patient info, credentials, or anything
with PHI; no other Penciled repo. A guard hook enforces this: if a call is blocked, do not
work around it; report it in OUTPUT. Never git push.

Assigned packet:
${TASK_BODY}

Complete the engineering task (implement, do not only describe).
1. inspect scoped implementation 2. implement 3. run ACCEPTANCE checks
4. fix your failures 5. inspect diff 6. stay in SCOPE 7. commit if editing worktree
Never push to any remote. Do not ask the human routine questions. Only BLOCKED for
D-class product decisions or true blockers. Prefer committing working progress over thrashing.

At the end report:
STATUS: COMPLETE | BLOCKED | FAILED
PACKET:
ROLE: ${ROLE}
CLASS: ${CLASS}
SUMMARY:
TESTS:
VISUAL_DIFF: (frontend/design roles: scores + artifact paths; else n/a)
COMMIT:
PRODUCT_DECISION_REQUIRED:
RISKS:
EOF
)

write_launch_status() {
  local pid_val="$1"
  python3 - "$STATUS_FILE" <<PY
import json, os, time, sys
path = sys.argv[1]
doc = {
  "id": os.environ.get("PERSONA_WORKER_ID", ""),
  "alias": "$MODEL_ALIAS",
  "model": "$MODEL",
  "class": "$CLASS",
  "role": "$ROLE",
  "mode": "$WORKER_MODE",
  "state": "RUNNING",
  "pid": int("$pid_val") if str("$pid_val").isdigit() else None,
  "max_turns": int("$MAX_TURNS"),
  "observed_turns": 0,
  "observed_turns_source": "none",
  "budget_pct": 0,
  "budgetTurns": int("$MAX_TURNS"),
  "observedTurns": 0,
  "budgetPctApprox": 0,
  "cwd": "$WORKDIR",
  "worktree": "$WORKDIR",
  "branch": "$BRANCH",
  "head_at_start": "$HEAD_SHA",
  "headAtStart": "$HEAD_SHA",
  "packet": """${PACKET:-}""",
  "milestone": """$MILESTONE""",
  "started_at": float("$START_EPOCH"),
  "startedAt": int("$START_EPOCH"),
  "updated_at": time.time(),
  "updatedAt": int(time.time()),
  "last_output_at": time.time(),
  "last_meaningful_activity_at": time.time(),
  "stream_log": "$STREAM_LOG",
  "streamLog": "$STREAM_LOG",
  "stderr_log": "$STDERR_LOG",
  "exit_code": None,
  "note": "launched",
  "checkpoints": {"p40": False, "p70": False, "p85": False},
  "emCheckpoints": {"p40": False, "p70": False, "p85": False},
  "finish_transitioned": False,
  "recovery_count": int(os.environ.get("PERSONA_RECOVERY_COUNT", "0")),
  "parent_worker_id": os.environ.get("PERSONA_PARENT_WORKER_ID") or None,
  "git_dirty_summary": "",
  "git_status_hash": "",
}
json.dump(doc, open(path, "w"), indent=2)
open(path, "a").write("\n")
print("WORKER_STATUS", path)
PY
}

# Placeholder status before child starts (pid filled after launch)
write_launch_status "$$"

# Optional supervisor register hook
if [ -x "$SCRIPT_DIR/persona-supervisor.sh" ]; then
  "$SCRIPT_DIR/persona-supervisor.sh" register "$WORKER_ID" 2>/dev/null || true
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY_RUN claude_bin=$PERSONA_CLAUDE_BIN model=$MODEL alias=$MODEL_ALIAS role=$ROLE max_turns=$MAX_TURNS packet=${PACKET:-none} worker_id=$WORKER_ID mode=$WORKER_MODE status_dir=$STATUS_DIR"
  echo "PROMPT_CHARS=${#PROMPT}"
  exit 0
fi

git_dirty_summary() {
  (cd "$WORKDIR" && git status --porcelain 2>/dev/null | head -n 40) || true
}

finalize_status() {
  local exit_code="$1" end_state="$2" note="$3"
  local dirty
  dirty="$(git_dirty_summary)"
  python3 - "$STATUS_FILE" "$STREAM_LOG" "$exit_code" "$end_state" "$note" "$dirty" <<'PY'
import json, sys, time, hashlib
path, stream, code, state, note, dirty = sys.argv[1:7]
try:
    doc = json.load(open(path))
except Exception:
    doc = {}

observed = 0
source = "none"
final = None
seen_msg_ids = set()
try:
    with open(stream) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("type")
            if t == "result" or o.get("subtype") in ("success", "error_max_turns"):
                final = o
            if t == "assistant" or (
                isinstance(o.get("message"), dict) and o["message"].get("role") == "assistant"
            ):
                msg = o.get("message") if isinstance(o.get("message"), dict) else {}
                mid = msg.get("id")
                if mid:
                    if mid not in seen_msg_ids:
                        seen_msg_ids.add(mid)
                        observed += 1
                else:
                    observed += 1
            if isinstance(o.get("mock_turn"), int):
                observed = max(observed, o["mock_turn"])
except FileNotFoundError:
    pass

if final is not None and isinstance(final.get("num_turns"), int):
    observed = final["num_turns"]
    source = "result_num_turns"
elif observed > 0:
    source = "stream_estimate"

budget = int(doc.get("max_turns") or 0)
pct = int(observed * 100 / budget) if budget else 0
doc.update({
    "observed_turns": observed,
    "observedTurns": observed,
    "observed_turns_source": source,
    "budget_pct": pct,
    "budgetPctApprox": pct,
    "state": state,
    "note": note,
    "exit_code": int(code),
    "updated_at": time.time(),
    "updatedAt": int(time.time()),
    "elapsedSec": int(time.time() - float(doc.get("started_at") or time.time())),
    "git_dirty_summary": dirty,
    "git_status_hash": hashlib.sha256(dirty.encode()).hexdigest()[:16] if dirty else "",
})
if final is not None:
    doc["result_subtype"] = final.get("subtype")
json.dump(doc, open(path, "w"), indent=2)
open(path, "a").write("\n")
print(observed)
PY
}

# Acquire per-worktree lock (best-effort; supervisor also locks)
LOCK_KEY=$(python3 -c "import hashlib,os; print(hashlib.sha256(os.getcwd().encode()).hexdigest()[:16])")
LOCK_FILE="$STATUS_DIR/locks/${LOCK_KEY}.lock"
if [ -f "$LOCK_FILE" ]; then
  OLD_PID=$(python3 -c "import json; print(json.load(open('$LOCK_FILE')).get('pid',''))" 2>/dev/null || true)
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "persona-worker: worktree lock held by live pid $OLD_PID ($LOCK_FILE)" >&2
    if [ "$WORKER_MODE" != "finish" ] && [ "${PERSONA_FORCE_LOCK:-0}" != "1" ]; then
      exit 1
    fi
  fi
fi
python3 -c "import json,os,time; json.dump({'pid':os.getpid(),'worker_id':'$WORKER_ID','cwd':os.getcwd(),'at':time.time()}, open('$LOCK_FILE','w'), indent=2)"

cleanup_lock() {
  if [ -f "$LOCK_FILE" ]; then
    CUR=$(python3 -c "import json; print(json.load(open('$LOCK_FILE')).get('worker_id',''))" 2>/dev/null || true)
    if [ "$CUR" = "$WORKER_ID" ]; then
      rm -f "$LOCK_FILE"
    fi
  fi
}
trap cleanup_lock EXIT

set +e
CHILD_PID=""
if [ "${PERSONA_WORKER_MOCK:-0}" = "1" ]; then
  export PERSONA_CLAUDE_MAX_TURNS="$MAX_TURNS"
  export PERSONA_WORKER_CWD="$WORKDIR"
  export PERSONA_WORKER_ALIAS="$MODEL_ALIAS"
  export PERSONA_WORKER_MODEL="$MODEL"
  export PERSONA_WORKER_ROLE="$ROLE"
  export PERSONA_WORKER_MODE="$WORKER_MODE"
  # Mock writes the stream file itself; only capture stderr here.
  python3 "$SCRIPT_DIR/persona-mock-worker.py" \
    >/dev/null 2>"$STDERR_LOG" &
  CHILD_PID=$!
else
  "$PERSONA_CLAUDE_BIN" -p "$PROMPT" \
    --model "$MODEL" \
    --output-format stream-json \
    --verbose \
    --max-turns "$MAX_TURNS" \
    --dangerously-skip-permissions \
    < /dev/null >"$STREAM_LOG" 2>"$STDERR_LOG" &
  CHILD_PID=$!
fi

# Update status with real Claude/mock child PID (not bash $$)
write_launch_status "$CHILD_PID"
wait "$CHILD_PID"
CODE=$?
set -e

END_STATE="EXITED_FAILURE"
NOTE="exit $CODE"
if [ "$CODE" -eq 0 ]; then
  END_STATE="EXITED_SUCCESS"
  NOTE="success"
fi

LOGBLOB=$(cat "$STREAM_LOG" "$STDERR_LOG" 2>/dev/null || true)
RATE_LIMITED_HIT=0
if printf "%s" "$LOGBLOB" | grep -qiE 'session limit|api_error_status.:.?429|rate limit exceeded|hit your (usage )?limit|usage limit'; then
  RATE_LIMITED_HIT=1
fi
if [ "$RATE_LIMITED_HIT" -eq 0 ]; then
  RATE_LIMITED_HIT=$(python3 - "$STREAM_LOG" <<'PYRL' || true
import json, sys
path = sys.argv[1]
try:
    f = open(path)
except FileNotFoundError:
    print(0)
    raise SystemExit
for line in f:
    line = line.strip()
    if not line:
        continue
    try:
        o = json.loads(line)
    except Exception:
        continue
    if o.get("type") != "rate_limit_event":
        continue
    info = o.get("rate_limit_info") or {}
    status = str(info.get("status") or "").lower()
    if status in ("rejected", "rate_limited", "limited"):
        print(1)
        raise SystemExit
print(0)
PYRL
)
fi
if [ "${RATE_LIMITED_HIT:-0}" = "1" ]; then
  END_STATE="RATE_LIMITED"
  NOTE="rate_limited"
  CODE=75
fi
if printf "%s" "$LOGBLOB" | grep -qiE "error_max_turns|Reached maximum number of turns"; then
  END_STATE="HARD_CAP"
  NOTE="max_turns"
  CODE=76
fi
if [ "$CODE" -eq 76 ]; then
  END_STATE="HARD_CAP"
  NOTE="${NOTE:-max_turns}"
fi
if [ "$CODE" -eq 75 ]; then
  END_STATE="RATE_LIMITED"
  NOTE="${NOTE:-rate_limited}"
fi
if [ "$CODE" -eq 143 ] || [ "$CODE" -eq 15 ]; then
  END_STATE="INTERRUPTED"
  NOTE="sigterm"
fi

OBSERVED=$(finalize_status "$CODE" "$END_STATE" "$NOTE" || echo 0)

EVENTS="$STATUS_DIR/events.jsonl"
python3 - "$EVENTS" "$WORKER_ID" "$END_STATE" "$CODE" "$OBSERVED" <<'PY' 2>/dev/null || true
import json,sys,time
path, wid, state, code, turns = sys.argv[1:6]
evt = {
  "ts": time.time(),
  "type": "WORKER_EXITED",
  "worker_id": wid,
  "state": state,
  "exit_code": int(code),
  "observed_turns": int(turns or 0),
}
with open(path, "a") as f:
    f.write(json.dumps(evt) + "\n")
PY

if [ "$END_STATE" = "RATE_LIMITED" ]; then
  echo "persona-worker: RATE_LIMITED - defer B-high/C; do not downgrade to Grok" >&2
  exit 75
fi
if [ "$END_STATE" = "HARD_CAP" ]; then
  echo "persona-worker: HARD_CAP - preserve worktree; never reset; EM inspects / finish continuation" >&2
  echo "persona-worker: dirty summary:" >&2
  git_dirty_summary >&2 || true
  exit 76
fi
exit "$CODE"
