#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

[ "$EUID" -ne 0 ] || { echo "Do not run TmuxCue as root." >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
    echo "TmuxCue update requires a Git clone." >&2
    exit 1
}
[ -z "$(git status --porcelain)" ] || {
    echo "Refusing to update a dirty worktree. Commit or stash local changes first." >&2
    exit 1
}
git rev-parse --abbrev-ref '@{upstream}' >/dev/null 2>&1 || {
    echo "No upstream branch is configured." >&2
    exit 1
}

OLD_COMMIT="$(git rev-parse HEAD)"
TMUX_PID_BEFORE="$(pgrep -o -u "$(id -u)" tmux || true)"
PANES_BEFORE="$(tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}:#{pane_pid}' 2>/dev/null | sort || true)"
AI_BEFORE="$(ps -eo uid=,pid=,comm= | awk -v uid="$(id -u)" '$1 == uid && ($3 == "codex" || $3 == "claude" || $3 == "claude-code") { print $2 ":" $3 }' | sort)"

rollback() {
    status=$?
    if [ "$status" -ne 0 ] && [ "$(git rev-parse HEAD)" != "$OLD_COMMIT" ]; then
        git reset --hard "$OLD_COMMIT" >/dev/null
    fi
}
trap rollback EXIT

git pull --ff-only
"$ROOT/install.sh"

TMUX_PID_AFTER="$(pgrep -o -u "$(id -u)" tmux || true)"
PANES_AFTER="$(tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}:#{pane_pid}' 2>/dev/null | sort || true)"
AI_AFTER="$(ps -eo uid=,pid=,comm= | awk -v uid="$(id -u)" '$1 == uid && ($3 == "codex" || $3 == "claude" || $3 == "claude-code") { print $2 ":" $3 }' | sort)"
[ "$TMUX_PID_BEFORE" = "$TMUX_PID_AFTER" ] \
    && [ "$PANES_BEFORE" = "$PANES_AFTER" ] \
    && [ "$AI_BEFORE" = "$AI_AFTER" ] || {
        echo "Safety check failed: a running terminal process changed during update." >&2
        exit 1
    }

echo "TmuxCue updated to $("$HOME/.local/bin/tmuxcue" --version)."
