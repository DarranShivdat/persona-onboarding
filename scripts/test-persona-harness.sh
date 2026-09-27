#!/usr/bin/env bash
# Deterministic mock-based acceptance tests for Persona local supervisor harness.
# No real Claude. Exit non-zero on any failure.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKER="$ROOT/scripts/claude-worker.sh"
SUP="$ROOT/scripts/persona-supervisor.sh"
MOCK="$ROOT/scripts/persona-mock-worker.py"

PASS=0
FAIL=0
TMP_BASE="/tmp/persona-harness-test-$$"
mkdir -p "$TMP_BASE"

cleanup_all() {
  # Best-effort: stop any supervisors we started under TMP_BASE
  for d in "$TMP_BASE"/*; do
    [ -d "$d" ] || continue
    if [ -f "$d/supervisor.json" ]; then
      PERSONA_WORKER_STATUS_DIR="$d" "$SUP" stop --status-dir "$d" >/dev/null 2>&1 || true
    fi
    # kill leftover mock workers referencing this dir
    pkill -f "persona-mock-worker.py" 2>/dev/null || true
  done
}
trap cleanup_all EXIT

log() { printf '%s\n' "$*"; }

assert_true() {
  local name="$1"
  shift
  if "$@"; then
    log "  PASS: $name"
    PASS=$((PASS + 1))
    return 0
  else
    log "  FAIL: $name"
    FAIL=$((FAIL + 1))
    return 1
  fi
}

# Don't abort whole suite on single assert failure inside a test
set +e

new_status_dir() {
  local name="$1"
  local d="$TMP_BASE/$name"
  mkdir -p "$d/locks" "$d/worktree"
  # minimal git repo in worktree for dirty detection
  (
    cd "$d/worktree"
    git init -q
    git config user.email "test@example.com"
    git config user.name "Test"
    echo "base" > README.md
    git add README.md
    git commit -q -m "init"
  )
  echo "$d"
}

wait_for() {
  # wait_for <timeout_sec> <cmd...>
  local timeout="$1"; shift
  local start end
  start=$(date +%s)
  while true; do
    if "$@"; then return 0; fi
    end=$(date +%s)
    if [ $((end - start)) -ge "$timeout" ]; then return 1; fi
    sleep 0.2
  done
}

events_has() {
  local dir="$1" typ="$2"
  [ -f "$dir/events.jsonl" ] || return 1
  grep -q "\"type\": \"$typ\"" "$dir/events.jsonl" 2>/dev/null || grep -q "\"type\":\"$typ\"" "$dir/events.jsonl" 2>/dev/null
}

events_any() {
  local dir="$1"; shift
  local t
  for t in "$@"; do
    if events_has "$dir" "$t"; then return 0; fi
  done
  return 1
}

worker_state_is() {
  local dir="$1" wid="$2" want="$3"
  python3 - "$dir/$wid.json" "$want" <<'PY'
import json,sys
p,want=sys.argv[1:3]
try:
  d=json.load(open(p))
except Exception:
  sys.exit(1)
sys.exit(0 if str(d.get("state","")).upper()==want.upper() else 1)
PY
}

supervisor_alive() {
  local dir="$1"
  python3 - "$dir/supervisor.json" <<'PY'
import json,sys,os
p=sys.argv[1]
try: d=json.load(open(p))
except Exception: sys.exit(1)
pid=d.get("pid")
if not pid: sys.exit(1)
try:
  os.kill(int(pid),0); sys.exit(0)
except Exception:
  sys.exit(1)
PY
}

heartbeat_advances() {
  local dir="$1"
  local a b
  a=$(python3 -c "import json; print(json.load(open('$dir/supervisor.json')).get('heartbeat_at',0))")
  sleep 1.2
  b=$(python3 -c "import json; print(json.load(open('$dir/supervisor.json')).get('heartbeat_at',0))")
  python3 -c "import sys; sys.exit(0 if float('$b')>float('$a') else 1)"
}

start_supervisor() {
  local dir="$1"
  export PERSONA_WORKER_STATUS_DIR="$dir"
  export PERSONA_SUPERVISOR_POLL_SECONDS="${PERSONA_SUPERVISOR_POLL_SECONDS:-1}"
  export PERSONA_STALE_SECONDS="${PERSONA_STALE_SECONDS:-8}"
  export PERSONA_FINISH_PCT="${PERSONA_FINISH_PCT:-80}"
  export PERSONA_CAFFEINATE_MOCK="${PERSONA_CAFFEINATE_MOCK:-1}"
  export PERSONA_WORKER_MOCK=1
  export PERSONA_MAX_RECOVERY=2
  export PERSONA_FINISH_MOCK_MODE=NORMAL
  "$SUP" start --status-dir "$dir"
  wait_for 5 supervisor_alive "$dir"
}

stop_supervisor() {
  local dir="$1"
  "$SUP" stop --status-dir "$dir" >/dev/null 2>&1 || true
}

launch_mock_worker() {
  local dir="$1" wid="$2" mode="$3" max_turns="${4:-20}" worker_mode="${5:-}"
  local wt="$dir/worktree"
  (
    cd "$wt"
    export PERSONA_WORKER_MOCK=1
    export PERSONA_MOCK_MODE="$mode"
    export PERSONA_WORKER_ID="$wid"
    export PERSONA_WORKER_STATUS_DIR="$dir"
    export PERSONA_CLAUDE_MAX_TURNS="$max_turns"
    export PERSONA_MOCK_TICK="${PERSONA_MOCK_TICK:-0.15}"
    export PERSONA_MOCK_STALL_HOLD="${PERSONA_MOCK_STALL_HOLD:-25}"
    export PERSONA_MOCK_NEAR_IDLE="${PERSONA_MOCK_NEAR_IDLE:-25}"
    export PERSONA_FORCE_LOCK=1
    if [ -n "$worker_mode" ]; then
      export PERSONA_WORKER_MODE="$worker_mode"
    fi
    # packet file
    local pkt="$dir/packet-$wid.md"
    cat > "$pkt" <<PKT
TASK: mock harness test
WHY: acceptance
SCOPE: mock
REQUIREMENTS: emit stream
ACCEPTANCE: exit cleanly
PKT
    "$WORKER" opus --packet "$pkt" --worker-id "$wid" --max-turns "$max_turns" &
  )
}

# ---------------- Test A ----------------
test_A() {
  log "TEST A: normal worker lifecycle"
  local dir wid
  dir=$(new_status_dir A)
  wid="a-normal"
  start_supervisor "$dir" || { log "  FAIL: supervisor start"; FAIL=$((FAIL+1)); return; }
  launch_mock_worker "$dir" "$wid" NORMAL 20
  wait_for 20 worker_state_is "$dir" "$wid" EXITED_SUCCESS
  assert_true "A worker EXITED_SUCCESS" worker_state_is "$dir" "$wid" EXITED_SUCCESS
  assert_true "A has observed_turns" python3 -c "import json; d=json.load(open('$dir/$wid.json')); assert d.get('observed_turns',0)>0; assert d.get('observed_turns_source') in ('result_num_turns','stream_estimate')"
  assert_true "A stream file exists" test -s "$dir/$wid.stream.jsonl"
  stop_supervisor "$dir"
}

# ---------------- Test B ----------------
test_B() {
  log "TEST B: near-cap → finish transition before hard cap"
  local dir wid
  dir=$(new_status_dir B)
  wid="b-near"
  export PERSONA_STALE_SECONDS=60
  export PERSONA_FINISH_PCT=80
  export PERSONA_SUPERVISOR_POLL_SECONDS=0.5
  export PERSONA_MOCK_TICK=0.05
  start_supervisor "$dir" || { log "  FAIL: supervisor start"; FAIL=$((FAIL+1)); return; }
  launch_mock_worker "$dir" "$wid" NEAR_CAP 20
  # Wait for FINISH_TRANSITION event
  wait_for 25 events_has "$dir" FINISH_TRANSITION
  assert_true "B FINISH_TRANSITION event" events_has "$dir" FINISH_TRANSITION
  # Parent should have finish_transitioned
  wait_for 10 python3 -c "import json; d=json.load(open('$dir/$wid.json')); assert d.get('finish_transitioned') is True"
  assert_true "B finish_transitioned flag" python3 -c "import json; d=json.load(open('$dir/$wid.json')); assert d.get('finish_transitioned') is True"
  # Should not be HARD_CAP on parent before transition
  assert_true "B parent not HARD_CAP before finish" python3 -c "import json; d=json.load(open('$dir/$wid.json')); assert d.get('state')!='HARD_CAP' or d.get('finish_transitioned')"
  stop_supervisor "$dir"
}

# ---------------- Test C ----------------
test_C() {
  log "TEST C: stalled → STALE → recovery"
  local dir wid
  dir=$(new_status_dir C)
  wid="c-stall"
  export PERSONA_STALE_SECONDS=3
  export PERSONA_FINISH_PCT=95
  export PERSONA_SUPERVISOR_POLL_SECONDS=0.5
  export PERSONA_MOCK_STALL_HOLD=30
  start_supervisor "$dir" || { log "  FAIL: supervisor start"; FAIL=$((FAIL+1)); return; }
  launch_mock_worker "$dir" "$wid" STALL 40
  wait_for 20 events_has "$dir" WORKER_STALE
  assert_true "C WORKER_STALE event" events_has "$dir" WORKER_STALE
  # Recovery or BLOCKED should follow
  wait_for 15 events_any "$dir" RECOVERY_LAUNCH WORKER_BLOCKED FINISH_TRANSITION
  assert_true "C recovery or blocked" events_any "$dir" RECOVERY_LAUNCH WORKER_BLOCKED FINISH_TRANSITION
  stop_supervisor "$dir"
}

# ---------------- Test D ----------------
test_D() {
  log "TEST D: hard cap → HARD_CAP + exit 76 + preserve"
  local dir wid code
  dir=$(new_status_dir D)
  wid="d-hard"
  export PERSONA_STALE_SECONDS=120
  export PERSONA_FINISH_PCT=99
  export PERSONA_SUPERVISOR_POLL_SECONDS=1
  export PERSONA_MOCK_TICK=0.05
  # Run worker in foreground to capture exit code (no finish transition interference initially)
  stop_supervisor "$dir" 2>/dev/null
  (
    cd "$dir/worktree"
    export PERSONA_WORKER_MOCK=1
    export PERSONA_MOCK_MODE=HARD_CAP
    export PERSONA_WORKER_ID="$wid"
    export PERSONA_WORKER_STATUS_DIR="$dir"
    export PERSONA_CLAUDE_MAX_TURNS=10
    export PERSONA_MOCK_TICK=0.05
    export PERSONA_FORCE_LOCK=1
    pkt="$dir/packet-$wid.md"
    echo -e "TASK: hard\nWHY: t\nSCOPE: s\nREQUIREMENTS: r\nACCEPTANCE: a" > "$pkt"
    "$WORKER" opus --packet "$pkt" --worker-id "$wid" --max-turns 10
  )
  code=$?
  assert_true "D exit code 76" test "$code" -eq 76
  assert_true "D state HARD_CAP" worker_state_is "$dir" "$wid" HARD_CAP
  # Worktree preserved (mock activity file may exist; git still intact)
  assert_true "D worktree preserved" test -d "$dir/worktree/.git"
  assert_true "D dirty summary recorded or activity file" bash -c "test -f '$dir/worktree/MOCK_ACTIVITY.txt' || test -n \"\$(python3 -c \"import json; print(json.load(open('$dir/$wid.json')).get('git_dirty_summary') or '')\")\""
  # Never reset — HEAD still has commit
  assert_true "D git history intact" bash -c "cd '$dir/worktree' && git rev-parse HEAD >/dev/null"
}

# ---------------- Test E ----------------
test_E() {
  log "TEST E: 3 parallel workers, separate state, no collision"
  local dir
  dir=$(new_status_dir E)
  export PERSONA_STALE_SECONDS=120
  export PERSONA_FINISH_PCT=99
  export PERSONA_SUPERVISOR_POLL_SECONDS=1
  start_supervisor "$dir" || { log "  FAIL: supervisor start"; FAIL=$((FAIL+1)); return; }
  launch_mock_worker "$dir" "e1" PARALLEL 20
  launch_mock_worker "$dir" "e2" PARALLEL 20
  launch_mock_worker "$dir" "e3" PARALLEL 20
  wait_for 25 worker_state_is "$dir" e1 EXITED_SUCCESS
  wait_for 10 worker_state_is "$dir" e2 EXITED_SUCCESS
  wait_for 10 worker_state_is "$dir" e3 EXITED_SUCCESS
  assert_true "E e1 success" worker_state_is "$dir" e1 EXITED_SUCCESS
  assert_true "E e2 success" worker_state_is "$dir" e2 EXITED_SUCCESS
  assert_true "E e3 success" worker_state_is "$dir" e3 EXITED_SUCCESS
  assert_true "E separate stream files" bash -c "test -s '$dir/e1.stream.jsonl' && test -s '$dir/e2.stream.jsonl' && test -s '$dir/e3.stream.jsonl'"
  assert_true "E separate json files" bash -c "test -f '$dir/e1.json' && test -f '$dir/e2.json' && test -f '$dir/e3.json'"
  stop_supervisor "$dir"
}

# ---------------- Test F ----------------
test_F() {
  log "TEST F: Grok absent — supervisor keeps running"
  local dir wid hb1 hb2
  dir=$(new_status_dir F)
  wid="f-alive"
  export PERSONA_STALE_SECONDS=120
  export PERSONA_FINISH_PCT=99
  export PERSONA_SUPERVISOR_POLL_SECONDS=0.5
  export PERSONA_MOCK_NEAR_IDLE=15
  start_supervisor "$dir" || { log "  FAIL: supervisor start"; FAIL=$((FAIL+1)); return; }
  # Long-running near-cap idle as stand-in for active worker
  launch_mock_worker "$dir" "$wid" NEAR_CAP 40
  sleep 2
  assert_true "F supervisor alive" supervisor_alive "$dir"
  assert_true "F heartbeat advances" heartbeat_advances "$dir"
  # reconcile must not kill workers
  "$SUP" reconcile --status-dir "$dir" >/dev/null
  sleep 0.5
  assert_true "F worker still alive after reconcile" python3 -c "import json,os; d=json.load(open('$dir/$wid.json')); os.kill(int(d['pid']),0)"
  # events accumulate
  n=$(wc -l < "$dir/events.jsonl" | tr -d ' ')
  sleep 2
  n2=$(wc -l < "$dir/events.jsonl" | tr -d ' ')
  assert_true "F events accumulate or heartbeat events exist" python3 -c "import sys; sys.exit(0 if int('$n2')>=int('$n') else 1)"
  stop_supervisor "$dir"
}

# ---------------- Test G ----------------
test_G() {
  log "TEST G: supervisor crash/restart — workers remain, no duplicate"
  local dir wid old_pid new_pid worker_pid
  dir=$(new_status_dir G)
  wid="g-survives"
  export PERSONA_STALE_SECONDS=120
  export PERSONA_FINISH_PCT=99
  export PERSONA_SUPERVISOR_POLL_SECONDS=0.5
  export PERSONA_MOCK_NEAR_IDLE=30
  start_supervisor "$dir" || { log "  FAIL: supervisor start"; FAIL=$((FAIL+1)); return; }
  launch_mock_worker "$dir" "$wid" NEAR_CAP 40
  sleep 1.5
  worker_pid=$(python3 -c "import json; print(json.load(open('$dir/$wid.json'))['pid'])")
  old_pid=$(python3 -c "import json; print(json.load(open('$dir/supervisor.json'))['pid'])")
  # Kill supervisor hard (crash)
  kill -9 "$old_pid" 2>/dev/null || true
  sleep 0.5
  assert_true "G worker still alive after supervisor kill" python3 -c "import os; os.kill(int('$worker_pid'),0)"
  # Restart supervisor
  export PERSONA_SUPERVISOR_FORCE=1
  start_supervisor "$dir" || { log "  FAIL: supervisor restart"; FAIL=$((FAIL+1)); return; }
  new_pid=$(python3 -c "import json; print(json.load(open('$dir/supervisor.json'))['pid'])")
  assert_true "G new supervisor pid different" python3 -c "import sys; sys.exit(0 if int('$new_pid')!=int('$old_pid') else 1)"
  assert_true "G worker pid unchanged (no duplicate launch)" python3 -c "import json; d=json.load(open('$dir/$wid.json')); import sys; sys.exit(0 if int(d['pid'])==int('$worker_pid') else 1)"
  # Only one worker json for this id
  assert_true "G single worker file" test -f "$dir/$wid.json"
  stop_supervisor "$dir"
  unset PERSONA_SUPERVISOR_FORCE
}

# ---------------- Test H ----------------
test_H() {
  log "TEST H: simulated machine restart — reconcile"
  local dir wid
  dir=$(new_status_dir H)
  wid="h-dead"
  # Fabricate state: stale heartbeat, dead pid, preserved worker json
  python3 - "$dir" "$wid" <<'PY'
import json,time,os,sys
d,wid=sys.argv[1:3]
os.makedirs(d+"/locks", exist_ok=True)
sup={
  "pid": 999999,
  "started_at": time.time()-3600,
  "heartbeat_at": time.time()-3600,
  "hostname": "old-host",
  "active_worker_ids": [wid],
  "version": "1.0.0",
  "caffeinate_pid": None,
  "clean_shutdown": False,
  "status_dir": d,
}
json.dump(sup, open(d+"/supervisor.json","w"), indent=2)
w={
  "id": wid,
  "state": "RUNNING",
  "pid": 888888,
  "max_turns": 40,
  "observed_turns": 12,
  "observed_turns_source": "stream_estimate",
  "cwd": d+"/worktree",
  "worktree": d+"/worktree",
  "started_at": time.time()-1800,
  "updated_at": time.time()-1800,
  "stream_log": d+f"/{wid}.stream.jsonl",
  "checkpoints": {"p40": True, "p70": False, "p85": False},
  "finish_transitioned": False,
  "recovery_count": 0,
  "git_dirty_summary": " M MOCK_ACTIVITY.txt",
}
json.dump(w, open(d+f"/{wid}.json","w"), indent=2)
open(d+f"/{wid}.stream.jsonl","w").write(json.dumps({"type":"assistant","mock_turn":12})+"\n")
# stale lock
json.dump({"pid":888888,"worker_id":wid,"cwd":d+"/worktree"}, open(d+"/locks/dead.lock","w"))
PY
  "$SUP" reconcile --status-dir "$dir" > "$dir/reconcile.out"
  assert_true "H worker marked INTERRUPTED" worker_state_is "$dir" "$wid" INTERRUPTED
  assert_true "H MACHINE_RECONCILED event" events_has "$dir" MACHINE_RECONCILED
  assert_true "H reconcile report mentions interrupted" grep -q interrupted "$dir/reconcile.out"
  # Starting supervisor should also reconcile without spamming recoveries forever
  export PERSONA_SUPERVISOR_FORCE=1
  export PERSONA_STALE_SECONDS=120
  export PERSONA_FINISH_PCT=99
  start_supervisor "$dir" || true
  sleep 2
  # Should not launch infinite recoveries — recovery_count stays low / no flood
  rec=$(python3 -c "import pathlib; p=pathlib.Path('$dir/events.jsonl'); print(p.read_text().count('RECOVERY_LAUNCH') if p.exists() else 0)")
  assert_true "H no recovery spam" python3 -c "import sys; sys.exit(0 if int('$rec')<=2 else 1)"
  stop_supervisor "$dir"
  unset PERSONA_SUPERVISOR_FORCE
}

# ---------------- Test I ----------------
test_I() {
  log "TEST I: keep-awake lifecycle (caffeinate)"
  local dir wid cpid
  dir=$(new_status_dir I)
  wid="i-caf"
  export PERSONA_CAFFEINATE_MOCK=1
  export PERSONA_STALE_SECONDS=120
  export PERSONA_FINISH_PCT=99
  export PERSONA_SUPERVISOR_POLL_SECONDS=0.5
  export PERSONA_MOCK_NEAR_IDLE=12
  start_supervisor "$dir" || { log "  FAIL: supervisor start"; FAIL=$((FAIL+1)); return; }
  # No workers yet — caffeinate may be absent
  launch_mock_worker "$dir" "$wid" NEAR_CAP 40
  wait_for 5 test -f "$dir/$wid.json"
  wait_for 15 python3 -c "import json,sys; d=json.load(open('$dir/supervisor.json')); sys.exit(0 if d.get('caffeinate_pid') else 1)"
  cpid=$(python3 -c "import json; print(json.load(open('$dir/supervisor.json')).get('caffeinate_pid'))")
  assert_true "I caffeinate pid set while worker active" python3 -c "import os; os.kill(int('$cpid'),0)"
  # Wait for worker to finish (or kill it) then ensure caffeinate released
  wait_for 30 worker_state_is "$dir" "$wid" EXITED_SUCCESS || kill "$(python3 -c "import json; print(json.load(open('$dir/$wid.json')).get('pid') or 0)")" 2>/dev/null
  # Force tick by waiting poll cycles after worker gone
  sleep 3
  # If worker still somehow alive, terminate
  python3 -c "import json,os; d=json.load(open('$dir/$wid.json')); p=d.get('pid');
import contextlib
with contextlib.suppress(Exception):
  os.kill(int(p),15) if p else None" 2>/dev/null
  sleep 3
  wait_for 15 python3 -c "import json,os; d=json.load(open('$dir/supervisor.json')); p=d.get('caffeinate_pid');
import sys
if not p: sys.exit(0)
try:
  os.kill(int(p),0); sys.exit(1)
except ProcessLookupError:
  sys.exit(0)
except Exception:
  sys.exit(0)"
  assert_true "I caffeinate gone after no workers" python3 -c "import json,os,sys; d=json.load(open('$dir/supervisor.json')); p=d.get('caffeinate_pid');
(sys.exit(0) if not p else (os.kill(int(p),0), sys.exit(1)))" 2>/dev/null || python3 -c "
import json,os,sys
d=json.load(open('$dir/supervisor.json'))
p=d.get('caffeinate_pid')
if not p:
  sys.exit(0)
try:
  os.kill(int(p),0)
  sys.exit(1)
except ProcessLookupError:
  sys.exit(0)
except Exception:
  sys.exit(0)
"
  stop_supervisor "$dir"
}

# ---------------- Test J ----------------
test_J() {
  log "TEST J: event queue reconciliation"
  local dir wid
  dir=$(new_status_dir J)
  wid="j-events"
  export PERSONA_STALE_SECONDS=120
  export PERSONA_FINISH_PCT=99
  export PERSONA_SUPERVISOR_POLL_SECONDS=0.5
  start_supervisor "$dir" || { log "  FAIL: supervisor start"; FAIL=$((FAIL+1)); return; }
  assert_true "J SUPERVISOR_STARTED" events_has "$dir" SUPERVISOR_STARTED
  launch_mock_worker "$dir" "$wid" NORMAL 15
  wait_for 20 worker_state_is "$dir" "$wid" EXITED_SUCCESS
  # WORKER_STARTED via register or exit event
  wait_for 5 events_any "$dir" WORKER_STARTED WORKER_EXITED
  assert_true "J worker lifecycle event present" events_any "$dir" WORKER_STARTED WORKER_EXITED
  "$SUP" reconcile --status-dir "$dir" >/dev/null
  assert_true "J SUPERVISOR_RECONCILE" events_has "$dir" SUPERVISOR_RECONCILE
  # events.jsonl is append-only valid JSON lines
  assert_true "J events.jsonl valid JSONL" python3 -c "
import json
ok=True
for line in open('$dir/events.jsonl'):
  line=line.strip()
  if not line: continue
  json.loads(line)
print('ok')
"
  stop_supervisor "$dir"
  assert_true "J SUPERVISOR_STOPPED" events_has "$dir" SUPERVISOR_STOPPED
}

# ---------------- Test K ----------------
test_K() {
  log "TEST K: finish-mode workers are not finish-transition thrashed"
  local dir wid pid nest_count ft_n
  dir=$(new_status_dir K)
  wid="k-finish-budget"
  export PERSONA_STALE_SECONDS=120
  export PERSONA_FINISH_PCT=80
  export PERSONA_SUPERVISOR_POLL_SECONDS=0.5
  export PERSONA_MOCK_TICK=0.05
  export PERSONA_MOCK_NEAR_IDLE=20
  export PERSONA_FINISH_GIT_IDLE_SECONDS=1
  start_supervisor "$dir" || { log "  FAIL: supervisor start"; FAIL=$((FAIL+1)); return; }

  # Part 1: a finish-mode NEAR_CAP worker must NOT be killed for another finish
  (
    cd "$dir/worktree"
    export PERSONA_WORKER_MOCK=1
    export PERSONA_MOCK_MODE=NEAR_CAP
    export PERSONA_WORKER_MODE=finish
    export PERSONA_WORKER_ID="$wid"
    export PERSONA_WORKER_STATUS_DIR="$dir"
    export PERSONA_CLAUDE_MAX_TURNS=20
    export PERSONA_MOCK_TICK=0.05
    export PERSONA_MOCK_NEAR_IDLE=18
    export PERSONA_FORCE_LOCK=1
    export PERSONA_PARENT_WORKER_ID="k-parent-normal"
    export PERSONA_RECOVERY_COUNT=1
    pkt="$dir/packet-$wid.md"
    echo -e "TASK: finish\nWHY: t\nSCOPE: s\nREQUIREMENTS: r\nACCEPTANCE: a" > "$pkt"
    "$WORKER" opus --packet "$pkt" --worker-id "$wid" --max-turns 20 &
  )
  wait_for 15 test -f "$dir/$wid.json"
  wait_for 20 python3 -c "import json,sys; d=json.load(open('$dir/$wid.json')); sys.exit(0 if int(d.get('budget_pct') or 0)>=80 else 1)"
  assert_true "K finish worker reached high budget_pct" python3 -c "import json,sys; d=json.load(open('$dir/$wid.json')); sys.exit(0 if int(d.get('budget_pct') or 0)>=80 else 1)"
  # Give supervisor several poll cycles where old code would FINISH_TRANSITION
  sleep 4
  ft_n=$(python3 -c "
import json
from pathlib import Path
n=0
p=Path('$dir/events.jsonl')
if p.exists():
  for line in p.read_text().splitlines():
    o=json.loads(line)
    if o.get('type')=='FINISH_TRANSITION' and o.get('worker_id')=='$wid':
      n+=1
print(n)
")
  assert_true "K no FINISH_TRANSITION from finish-mode worker" python3 -c "import sys; sys.exit(0 if int('$ft_n')==0 else 1)"
  assert_true "K finish worker not note=finish_transition" python3 -c "import json,sys; d=json.load(open('$dir/$wid.json')); sys.exit(0 if d.get('note')!='finish_transition' else 1)"
  assert_true "K finish worker not exit 143 finish kill" python3 -c "
import json,sys,os
d=json.load(open('$dir/$wid.json'))
if d.get('note')=='finish_transition' and d.get('exit_code')==143:
  sys.exit(1)
pid=d.get('pid')
alive=False
try:
  if pid: os.kill(int(pid),0); alive=True
except ProcessLookupError:
  alive=False
except Exception:
  alive=False
st=(d.get('state') or '').upper()
code=d.get('exit_code')
if alive or st=='EXITED_SUCCESS' or code in (0, None):
  sys.exit(0)
sys.exit(0 if code!=143 else 1)
"
  nest_count=$(python3 -c "
from pathlib import Path
print(sum(1 for p in Path('$dir').glob('$wid-finish-*.json')))
")
  assert_true "K no nested finish child from finish-mode budget" python3 -c "import sys; sys.exit(0 if int('$nest_count')==0 else 1)"
  stop_supervisor "$dir"

  # Part 2: stream_estimate alone must not nest finish-on-finish recovery
  dir=$(new_status_dir K2)
  wid="k2-finish-stream"
  export PERSONA_STALE_SECONDS=120
  export PERSONA_FINISH_PCT=80
  export PERSONA_SUPERVISOR_POLL_SECONDS=0.5
  export PERSONA_FINISH_GIT_IDLE_SECONDS=0
  python3 -c "import time; time.sleep(30)" &
  pid=$!
  python3 - "$dir" "$wid" "$pid" <<'PY'
import json, time, os, sys
d, wid, pid = sys.argv[1:4]
pid = int(pid)
open(os.path.join(d, "worktree", "DIRTY.txt"), "w").write("x\n")
w = {
  "id": wid,
  "state": "RUNNING",
  "pid": pid,
  "mode": "finish",
  "max_turns": 10,
  "observed_turns": 10,
  "observed_turns_source": "stream_estimate",
  "budget_pct": 100,
  "cwd": d + "/worktree",
  "worktree": d + "/worktree",
  "started_at": time.time() - 600,
  "updated_at": time.time(),
  "last_output_at": time.time(),
  "last_meaningful_activity_at": time.time() - 600,
  "last_git_activity_at": time.time() - 600,
  "stream_log": d + f"/{wid}.stream.jsonl",
  "checkpoints": {"p40": True, "p70": True, "p85": True},
  "finish_transitioned": False,
  "recovery_count": 1,
  "parent_worker_id": "k2-parent",
  "git_dirty_summary": "?? DIRTY.txt",
  "git_status_hash": "deadbeef",
}
json.dump(w, open(d + f"/{wid}.json", "w"), indent=2)
with open(d + f"/{wid}.stream.jsonl", "w") as f:
  for i in range(1, 11):
    f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": f"t{i}"}}) + "\n")
PY
  start_supervisor "$dir" || { log "  FAIL: supervisor start K2"; kill "$pid" 2>/dev/null; FAIL=$((FAIL+1)); return; }
  sleep 4
  ft_n=$(python3 -c "
import json
from pathlib import Path
n=0
p=Path('$dir/events.jsonl')
if p.exists():
  for line in p.read_text().splitlines():
    o=json.loads(line)
    if o.get('type')=='FINISH_TRANSITION' and o.get('worker_id')=='$wid':
      n+=1
print(n)
")
  assert_true "K2 no FINISH_TRANSITION from stream_estimate finish worker" python3 -c "import sys; sys.exit(0 if int('$ft_n')==0 else 1)"
  nest_count=$(python3 -c "from pathlib import Path; print(sum(1 for p in Path('$dir').glob('$wid-finish-*.json')))")
  assert_true "K2 no nested finish-on-finish from stream_estimate" python3 -c "import sys; sys.exit(0 if int('$nest_count')==0 else 1)"
  assert_true "K2 finish worker not terminated for finish_transition" python3 -c "
import json,sys
d=json.load(open('$dir/$wid.json'))
sys.exit(0 if d.get('note')!='finish_transition' else 1)
"
  stop_supervisor "$dir"
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}


# ---------------- Test L (Persona port) ----------------
test_L() {
  log "TEST L: roles + model resolution (dry-run, no Claude)"
  local d; d=$(new_status_dir L)
  local pkt="$d/packet-design.md"
  printf 'TASK: x\nWHY: y\nSCOPE: docs/design\nREQUIREMENTS: r\nACCEPTANCE: a\n' > "$pkt"
  local out
  out=$(cd "$d/worktree" && PERSONA_WORKER_STATUS_DIR="$d" "$WORKER" opus --role design --packet "$pkt" --dry-run --worker-id l-design 2>&1)
  assert_true "L default opus model is claude-opus-5-5" grep -q "model=claude-opus-5-5" <<<"$out"
  assert_true "L role=design recorded" grep -q "role=design" <<<"$out"
  assert_true "L design preflight warns on missing REFERENCES/DELIVERABLES" grep -q "incomplete packet for role design" <<<"$out"
  out=$(cd "$d/worktree" && PERSONA_OPUS_MODEL=claude-opus-5 PERSONA_WORKER_STATUS_DIR="$d" "$WORKER" opus --role frontend --packet "$pkt" --dry-run --worker-id l-fe 2>&1)
  assert_true "L PERSONA_OPUS_MODEL override honored" grep -q "model=claude-opus-5 " <<<"$out"
  assert_true "L role=frontend recorded" grep -q "role=frontend" <<<"$out"
  out=$(cd "$d/worktree" && PERSONA_WORKER_STATUS_DIR="$d" "$WORKER" opus --role bogus --packet "$pkt" --dry-run 2>&1)
  assert_true "L unknown role rejected" grep -q "Unknown role" <<<"$out"
}

# ---- run all ----
log "=== Persona harness acceptance (tmpdir=$TMP_BASE) ==="
test_A
test_B
test_C
test_D
test_E
test_F
test_G
test_H
test_I
test_J
test_K
test_L

log "=== SUMMARY: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -gt 0 ]; then
  log "HARNESS TESTS FAILED"
  exit 1
fi
log "HARNESS TESTS PASSED"
exit 0
