#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

[ "$EUID" -ne 0 ] || { echo "Do not run TmuxCue as root." >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="$HOME/.local/share/tmuxcue"
BIN_DIR="$HOME/.local/bin"
SERVICE="$HOME/.config/systemd/user/tmuxcue.service"

TMUX_PID_BEFORE="$(pgrep -o -u "$(id -u)" tmux || true)"
PANES_BEFORE="$(tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}:#{pane_pid}' 2>/dev/null | sort || true)"
AI_BEFORE="$(ps -eo uid=,pid=,comm= | awk -v uid="$(id -u)" '$1 == uid && ($3 == "codex" || $3 == "claude" || $3 == "claude-code") { print $2 ":" $3 }' | sort)"

PYTHONPATH="$ROOT/src" python3 -m tmuxcue.vscode uninstall
PYTHONPATH="$ROOT/src" python3 -c 'from tmuxcue.settings import apply_title_mode; apply_title_mode("native")'
systemctl --user disable --now tmuxcue.service >/dev/null 2>&1 || true
rm -f "$SERVICE" "$BIN_DIR/tmuxcue" "$BIN_DIR/tmuxcue-hook" "$BIN_DIR/tmuxcue-service"
rm -f "$HOME/.local/share/bash-completion/completions/tmuxcue" \
    "$HOME/.local/share/zsh/site-functions/_tmuxcue" \
    "$HOME/.config/fish/completions/tmuxcue.fish"
systemctl --user daemon-reload
python3 "$ROOT/scripts/configure.py" uninstall

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
AI_AFTER="$(ps -eo uid=,pid=,comm= | awk -v uid="$(id -u)" '$1 == uid && ($3 == "codex" || $3 == "claude" || $3 == "claude-code") { print $2 ":" $3 }' | sort)"
[ "$TMUX_PID_BEFORE" = "$TMUX_PID_AFTER" ] \
    && [ "$PANES_BEFORE" = "$PANES_AFTER" ] \
    && [ "$AI_BEFORE" = "$AI_AFTER" ] || {
    echo "Safety check failed: a running terminal process changed during uninstall." >&2
    exit 1
}

rm -rf "$HOME/.config/tmuxcue"
[ "$BASE" = "$HOME/.local/share/tmuxcue" ] && rm -rf "$BASE"
echo "TmuxCue uninstalled. Existing tmux and terminal AI processes were left untouched."
