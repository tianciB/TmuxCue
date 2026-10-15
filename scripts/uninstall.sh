#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

[ "$EUID" -ne 0 ] || { echo "Do not run TmuxCue as root." >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${TMUXCUE_PYTHON:-$ROOT/python}"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3 || true)"
fi
[ -n "$PYTHON_BIN" ] && [ -x "$PYTHON_BIN" ] || {
    echo "TmuxCue requires Python 3.10 or newer to restore managed settings." >&2
    exit 1
}
"$PYTHON_BIN" - <<'PY'
import sys

if sys.version_info < (3, 10):
    raise SystemExit(
        f"TmuxCue requires Python 3.10 or newer; found {sys.version.split()[0]}."
    )
PY
BASE="$HOME/.local/share/tmuxcue"
BIN_DIR="$HOME/.local/bin"
SERVICE="$HOME/.config/systemd/user/tmuxcue.service"

TMUX_PID_BEFORE="$(pgrep -o -u "$(id -u)" tmux || true)"
PANES_BEFORE="$(tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}:#{pane_pid}' 2>/dev/null | sort || true)"
AI_BEFORE="$(PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -m tmuxcue __process-snapshot)"

PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -m tmuxcue.vscode uninstall
PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -c 'from tmuxcue.settings import apply_title_mode; apply_title_mode("native")'
systemctl --user disable --now tmuxcue.service >/dev/null 2>&1 || true
rm -f "$SERVICE" "$BIN_DIR/tmuxcue" "$BIN_DIR/tmuxcue-hook" "$BIN_DIR/tmuxcue-service"
rm -f "$HOME/.local/share/bash-completion/completions/tmuxcue" \
    "$HOME/.local/share/zsh/site-functions/_tmuxcue" \
    "$HOME/.config/fish/completions/tmuxcue.fish"
systemctl --user daemon-reload
"$PYTHON_BIN" "$ROOT/scripts/configure.py" uninstall

if tmux list-sessions >/dev/null 2>&1; then
    while IFS= read -r window; do
        for option in @agent_state @agent_icon @agent_title; do
            tmux set-option -wu -t "$window" "$option" >/dev/null 2>&1 || true
        done
    done < <(tmux list-windows -a -F '#{session_name}:#{window_index}')
    tmux refresh-client -S >/dev/null 2>&1 || true
fi

TMUX_PID_AFTER="$(pgrep -o -u "$(id -u)" tmux || true)"
PANES_AFTER="$(tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}:#{pane_pid}' 2>/dev/null | sort || true)"
AI_AFTER="$(PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -m tmuxcue __process-snapshot)"
[ "$TMUX_PID_BEFORE" = "$TMUX_PID_AFTER" ] \
    && [ "$PANES_BEFORE" = "$PANES_AFTER" ] \
    && [ "$AI_BEFORE" = "$AI_AFTER" ] || {
    echo "Safety check failed: a running terminal process changed during uninstall." >&2
    exit 1
}

rm -rf "$HOME/.config/tmuxcue"
[ "$BASE" = "$HOME/.local/share/tmuxcue" ] && rm -rf "$BASE"
echo "TmuxCue uninstalled. Existing tmux and terminal AI processes were left untouched."
