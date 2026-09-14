#!/usr/bin/env bash
# The only way to run commands inside an M2 worktree (C:/Projects/sed-wt/<ws>).
# Usage (absolute, unquoted path): bash C:/Projects/sed-wt/<ws>/scripts/wt.sh python|git|npm|node ARGS...
# - runs from the worktree root with this worktree's src on PYTHONPATH (never main's code);
# - uses a scratch SED_DATA_ROOT per workstream (never the real %LOCALAPPDATA%\sed profiles);
# - never modifies PATH (the Unix `sed` stays first); `python` runs the main checkout's venv interpreter;
# - allows only non-destructive git subcommands (merge/rebase/push/checkout are integrator-only).
set -euo pipefail
to_win() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
WT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAIN="${SED_MAIN_CHECKOUT:-$(git -C "$WT" worktree list --porcelain | awk 'NR == 1 { sub(/^worktree /, ""); print }')}"
[ "$(to_win "$WT")" != "$(to_win "$MAIN")" ] || { echo "wt.sh: this is the main checkout; use C:/Projects/sed-wt/<ws>/scripts/wt.sh" >&2; exit 2; }
cd "$WT"
TOP="$(git rev-parse --show-toplevel)"
[ "$(to_win "$TOP")" = "$(to_win "$WT")" ] || { echo "wt.sh: $WT is not a worktree root" >&2; exit 2; }
WS="$(basename "$WT")"
DATA="${SED_WT_DATA:-/c/Projects/sed-wt/_data/$WS}"
mkdir -p "$DATA"
[ -f "$DATA/CLAUDE.md" ] || cp "$WT/CLAUDE.md" "$DATA/CLAUDE.md"
PY="$MAIN/.venv/Scripts/python.exe"
MACHINE_ROOT="${SED_DATA_ROOT:-${LOCALAPPDATA:+$LOCALAPPDATA/sed}}"  # the real data root, before the scratch override
export PYTHONPATH="$(to_win "$WT/src")"
export SED_REPO_ROOT="$(to_win "$WT")" SED_DATA_ROOT="$(to_win "$DATA")"
export SED_CLAUDE_SETTINGS_LOCAL="$(to_win "$DATA/settings.local.json")" SED_CLAUDE_MD="$(to_win "$DATA/CLAUDE.md")"
export UV_PROJECT_ENVIRONMENT="$(to_win "$MAIN/.venv")" UV_NO_SYNC=1 PYTHONUTF8=1
# Parallel worktrees must not share pytest's numbered temp dirs (pytest prunes other sessions' dirs). Each worktree
# gets its own temp root; inside it pytest keeps numbered, locked dirs, so two runs in one worktree do not wipe each
# other (a fixed --basetemp would be deleted at the start of every session).
mkdir -p "$DATA/pytest-tmp"
export PYTEST_DEBUG_TEMPROOT="$(to_win "$DATA/pytest-tmp")"
export PYTEST_ADDOPTS="-p no:cacheprovider"
unset SED_PROFILE SED_EXTRA_MODULES
if [ -n "$MACHINE_ROOT" ]; then
  export SED_GUARD_DENYLIST="${SED_GUARD_DENYLIST:-$(to_win "$MACHINE_ROOT/guard/denylist.txt")}"
fi
cmd="${1:-}"
[ $# -gt 0 ] && shift
case "$cmd" in
  python) exec "$PY" "$@" ;;
  npm|node) exec "$cmd" "$@" ;;
  git)
    case "${1:-}" in
      status|diff|add|commit|log|show|rev-parse|restore|rm|mv|ls-files) exec git "$@" ;;
      *) echo "wt.sh: git ${1:-} is integrator-only" >&2; exit 2 ;;
    esac ;;
  *) echo "wt.sh: first argument must be python, git, npm or node" >&2; exit 2 ;;
esac
