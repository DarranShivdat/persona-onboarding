# Shared helpers for scripts/deploy/*.sh (sourced; bash 3.2 compatible).
# Dry-run by default: every step is PRINTED; nothing touching the network or a cloud account
# runs unless --apply was given. Secret values are never printed — piped steps show only the
# variable name.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APPLY=0
STEP=0

parse_apply() {  # sets APPLY from args; leaves remaining args in REST (newline-separated)
  REST=""
  for a in "$@"; do
    case "$a" in
      --apply) APPLY=1 ;;
      *) REST="${REST}${a}
" ;;
    esac
  done
}

banner() {
  if [ "$APPLY" = 1 ]; then
    echo "== $1 — APPLY MODE: commands below WILL run =="
  else
    echo "== $1 — DRY RUN (nothing executed; re-run with --apply after the go-ahead) =="
  fi
}

step() { STEP=$((STEP + 1)); echo; echo "[$STEP] $*"; }

# run CMD ARGS... : print, and execute only with --apply.
run() {
  printf '  $'; printf ' %q' "$@"; printf '\n'
  if [ "$APPLY" = 1 ]; then "$@"; fi
}

# run_desc "shown text" CMD... : like run, but prints a description instead of the argv
# (for pipelines whose argv would contain a secret).
run_desc() {
  local shown="$1"; shift
  echo "  \$ $shown"
  if [ "$APPLY" = 1 ]; then "$@"; fi
}

manual() { echo "  MANUAL: $*"; }

need_tool() {  # only enforced in apply mode
  if [ "$APPLY" = 1 ] && ! command -v "$1" >/dev/null 2>&1; then
    echo "missing tool: $1 ($2)" >&2; exit 2
  fi
}

# check_env_file TARGET FILE : name-only report (local; no network). Dry-run tolerates absence.
check_env_file() {
  if [ -f "$2" ]; then
    python3 "$ROOT/scripts/check-env.py" --target "$1" --env-file "$2" || {
      if [ "$APPLY" = 1 ]; then echo "env check failed for $1 — fix $2 first" >&2; exit 1; fi
      echo "  (dry run: continuing despite the failed env check)"
    }
  else
    echo "  env file $2 not found — create it from $3 (gitignored .persona-deploy/)"
    if [ "$APPLY" = 1 ]; then exit 1; fi
  fi
}

# env_value NAME FILE : value of NAME from FILE for use in a pipe/argument. Never echo it.
env_value() {
  python3 "$ROOT/scripts/check-env.py" --emit "${3:-agent}" --env-file "$2" | sed -n "s/^$1=//p"
}

git_sha() { git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown; }

# AUDIT-001: --apply is gated on the LOCAL button audit (npm run qa:audit). --skip-audit overrides.
audit_gate() {  # audit_gate <skip:0|1>
  [ "$APPLY" = 1 ] || { echo "  (dry run: --apply would run 'npm run qa:audit' first and abort on failure)"; return 0; }
  if [ "$1" = 1 ]; then
    echo
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo "!!  WARNING: --skip-audit — deploying WITHOUT the button audit (qa:audit).  !!"
    echo "!!  Dead controls may ship. Run 'npm run qa:audit' before the next deploy.  !!"
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo
    return 0
  fi
  echo "  \$ npm run qa:audit"
  if ! (cd "$ROOT" && npm run qa:audit); then
    echo "ABORT: qa:audit failed — fix the dead controls (or pass --skip-audit, loudly)." >&2
    exit 1
  fi
}
