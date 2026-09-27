#!/usr/bin/env bash
# Thin wrapper around persona-supervisor.py (ported from FLOAT's float-supervisor).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/scripts/persona-supervisor.py" "$@"
