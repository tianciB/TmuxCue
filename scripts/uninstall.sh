#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

[ "$EUID" -ne 0 ] || { echo "Do not run TmuxCue as root." >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="$HOME/.local/share/tmuxcue"
BIN_DIR="$HOME/.local/bin"
SERVICE="$HOME/.config/systemd/user/tmuxcue.service"

systemctl --user disable --now tmuxcue.service >/dev/null 2>&1 || true
rm -f "$SERVICE" "$BIN_DIR/tmuxcue" "$BIN_DIR/tmuxcue-hook" "$BIN_DIR/tmuxcue-service"
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

[ "$BASE" = "$HOME/.local/share/tmuxcue" ] && rm -rf "$BASE"
echo "TmuxCue uninstalled. Existing tmux panes were left untouched."
