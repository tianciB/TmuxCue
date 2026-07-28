#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

[ "$EUID" -ne 0 ] || { echo "Do not run TmuxCue as root." >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="$HOME/.local/share/tmuxcue"
BIN_DIR="$HOME/.local/bin"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/tmuxcue.service"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
RELEASE="$BASE/releases/$STAMP"
TMP="$(mktemp -d)"
PREVIOUS="$(readlink "$BASE/current" 2>/dev/null || true)"
SERVICE_WAS_ACTIVE=0
systemctl --user is-active --quiet tmuxcue.service && SERVICE_WAS_ACTIVE=1
[ -f "$SERVICE_FILE" ] && cp "$SERVICE_FILE" "$TMP/previous.service"
COMMITTED=0

rollback() {
    status=$?
    if [ "$status" -ne 0 ] && [ "$COMMITTED" -eq 0 ]; then
        if [ -n "$PREVIOUS" ]; then
            ln -s "$PREVIOUS" "$TMP/current.rollback"
            mv -Tf "$TMP/current.rollback" "$BASE/current"
        else
            rm -f "$BASE/current"
        fi
        if [ -f "$TMP/previous.service" ]; then
            install -m 600 "$TMP/previous.service" "$SERVICE_FILE"
            systemctl --user daemon-reload
            [ "$SERVICE_WAS_ACTIVE" -eq 0 ] || systemctl --user restart tmuxcue.service || true
        else
            systemctl --user disable --now tmuxcue.service >/dev/null 2>&1 || true
            rm -f "$SERVICE_FILE"
            systemctl --user daemon-reload
        fi
    fi
    rm -rf "$TMP"
}
trap rollback EXIT

# Installation must never create, kill, attach, rename, or send keys to a pane.
TMUX_PID_BEFORE="$(pgrep -o -u "$(id -u)" tmux || true)"
PANES_BEFORE="$(tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}:#{pane_pid}' 2>/dev/null | sort || true)"

PYTHONPATH="$ROOT/src" python3 -m tmuxcue --check
PYTHONPATH="$ROOT/src" python3 -m unittest discover -s "$ROOT/tests" -q

mkdir -p "$RELEASE" "$BIN_DIR" "$SERVICE_DIR"
cp -a "$ROOT/src" "$RELEASE/src"

cat > "$TMP/tmuxcue-service" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="$HOME/.local/share/tmuxcue/current/src"
export PYTHONDONTWRITEBYTECODE=1
exec /usr/bin/python3 -m tmuxcue "$@"
WRAPPER
cat > "$TMP/tmuxcue-hook" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="$HOME/.local/share/tmuxcue/current/src"
export PYTHONDONTWRITEBYTECODE=1
exec /usr/bin/python3 -m tmuxcue.hook "$@"
WRAPPER
cat > "$TMP/tmuxcue" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="$HOME/.local/share/tmuxcue/current/src"
export PYTHONDONTWRITEBYTECODE=1
exec /usr/bin/python3 -m tmuxcue "$@"
WRAPPER
install -m 700 "$TMP/tmuxcue-service" "$TMP/tmuxcue-hook" "$TMP/tmuxcue" "$BIN_DIR/"

ln -s "$RELEASE" "$TMP/current"
mkdir -p "$BASE"
mv -Tf "$TMP/current" "$BASE/current"

cp "$ROOT/systemd/tmuxcue.service" "$TMP/tmuxcue.service"
systemd-analyze --user verify "$TMP/tmuxcue.service" >/dev/null
install -m 600 "$TMP/tmuxcue.service" "$SERVICE_FILE"
systemctl --user daemon-reload
systemctl --user enable tmuxcue.service >/dev/null
systemctl --user restart tmuxcue.service
systemctl --user is-active --quiet tmuxcue.service

# Only after the new service is healthy do we update persistent configuration.
python3 "$ROOT/scripts/configure.py" install --root "$ROOT"

# Apply four display options directly. Never source the complete tmux config.
if tmux list-sessions >/dev/null 2>&1; then
    tmux set-option -g set-titles on
    tmux set-option -g set-titles-string '#{?#{@agent_title},#{@agent_title},tmux #S:#I:#W}'
    tmux set-option -g window-status-format ' #{@agent_icon} #I:#W#{?window_flags,#{window_flags}, } '
    tmux set-option -g window-status-current-format ' #{@agent_icon} #I:#W#{?window_flags,#{window_flags}, } '
    tmux refresh-client -S >/dev/null 2>&1 || true
fi

TMUX_PID_AFTER="$(pgrep -o -u "$(id -u)" tmux || true)"
PANES_AFTER="$(tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}:#{pane_pid}' 2>/dev/null | sort || true)"
[ "$TMUX_PID_BEFORE" = "$TMUX_PID_AFTER" ] && [ "$PANES_BEFORE" = "$PANES_AFTER" ] || {
    echo "Safety check failed: tmux process or panes changed during installation." >&2
    exit 1
}

COMMITTED=1
echo "TmuxCue installed. Existing tmux panes were left untouched."
echo "Codex users: run /hooks once to review and trust the hooks."
