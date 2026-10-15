#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

[ "$EUID" -ne 0 ] || { echo "Do not run TmuxCue as root." >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_CANDIDATE="${TMUXCUE_PYTHON:-$(command -v python3 || true)}"
[ -n "$PYTHON_CANDIDATE" ] && [ -x "$PYTHON_CANDIDATE" ] || {
    echo "TmuxCue requires Python 3.10 or newer, but python3 was not found." >&2
    exit 1
}
"$PYTHON_CANDIDATE" - <<'PY'
import sys

if sys.version_info < (3, 10):
    raise SystemExit(
        f"TmuxCue requires Python 3.10 or newer; found {sys.version.split()[0]}."
    )
PY
PYTHON_BIN="$("$PYTHON_CANDIDATE" -c 'import os, sys; print(os.path.realpath(sys.executable))')"
[ -x "$PYTHON_BIN" ] || {
    echo "TmuxCue could not resolve a usable Python interpreter." >&2
    exit 1
}
BASE="$HOME/.local/share/tmuxcue"
BIN_DIR="$HOME/.local/bin"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/tmuxcue.service"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
RELEASE="$BASE/releases/$STAMP"
TMP="$(mktemp -d)"
PREVIOUS="$(readlink "$BASE/current" 2>/dev/null || true)"
PREVIOUS_MODE="$(PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -c 'from tmuxcue.settings import title_mode; print(title_mode())')"
TITLE_MODE_APPLIED=0
SERVICE_WAS_ACTIVE=0
systemctl --user is-active --quiet tmuxcue.service && SERVICE_WAS_ACTIVE=1
[ -f "$SERVICE_FILE" ] && cp "$SERVICE_FILE" "$TMP/previous.service"
for name in tmuxcue tmuxcue-hook tmuxcue-service; do
    [ ! -f "$BIN_DIR/$name" ] || cp -p "$BIN_DIR/$name" "$TMP/previous-$name"
done
COMMITTED=0

rollback() {
    status=$?
    if [ "$status" -ne 0 ] && [ "$COMMITTED" -eq 0 ]; then
        if [ "$TITLE_MODE_APPLIED" -eq 1 ]; then
            PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -c "from tmuxcue.settings import apply_title_mode; apply_title_mode('$PREVIOUS_MODE')" || true
        fi
        if [ -n "$PREVIOUS" ]; then
            ln -s "$PREVIOUS" "$TMP/current.rollback"
            mv -Tf "$TMP/current.rollback" "$BASE/current"
        else
            rm -f "$BASE/current"
        fi
        for name in tmuxcue tmuxcue-hook tmuxcue-service; do
            if [ -f "$TMP/previous-$name" ]; then
                install -m 700 "$TMP/previous-$name" "$BIN_DIR/$name"
            else
                rm -f "$BIN_DIR/$name"
            fi
        done
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
AI_BEFORE="$(PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -m tmuxcue __process-snapshot)"

PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -m tmuxcue __check
PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -m unittest discover -s "$ROOT/tests" -q

mkdir -p "$RELEASE/scripts" "$BIN_DIR" "$SERVICE_DIR"
cp -a "$ROOT/src" "$ROOT/hooks" "$ROOT/tmux" "$RELEASE/"
cp -a "$ROOT/scripts/configure.py" "$ROOT/scripts/uninstall.sh" "$RELEASE/scripts/"
ln -s "$PYTHON_BIN" "$RELEASE/python"

cat > "$TMP/tmuxcue-service" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="$HOME/.local/share/tmuxcue/current/src"
export PYTHONDONTWRITEBYTECODE=1
export TMUXCUE_PYTHON="$HOME/.local/share/tmuxcue/current/python"
exec "$TMUXCUE_PYTHON" -m tmuxcue __service "$@"
WRAPPER
cat > "$TMP/tmuxcue-hook" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="$HOME/.local/share/tmuxcue/current/src"
export PYTHONDONTWRITEBYTECODE=1
export TMUXCUE_PYTHON="$HOME/.local/share/tmuxcue/current/python"
exec "$TMUXCUE_PYTHON" -m tmuxcue.hook "$@"
WRAPPER
cat > "$TMP/tmuxcue" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="$HOME/.local/share/tmuxcue/current/src"
export PYTHONDONTWRITEBYTECODE=1
export TMUXCUE_PYTHON="$HOME/.local/share/tmuxcue/current/python"
exec "$TMUXCUE_PYTHON" -m tmuxcue "$@"
WRAPPER
install -m 700 "$TMP/tmuxcue-service" "$TMP/tmuxcue-hook" "$TMP/tmuxcue" "$BIN_DIR/"

ln -s "$RELEASE" "$TMP/current"
mkdir -p "$BASE"
mv -Tf "$TMP/current" "$BASE/current"
"$BIN_DIR/tmuxcue" version >/dev/null

cp "$ROOT/systemd/tmuxcue.service" "$TMP/tmuxcue.service"
systemd-analyze --user verify "$TMP/tmuxcue.service" >/dev/null
install -m 600 "$TMP/tmuxcue.service" "$SERVICE_FILE"
systemctl --user daemon-reload
systemctl --user enable tmuxcue.service >/dev/null
systemctl --user restart tmuxcue.service
sleep 1
systemctl --user is-active --quiet tmuxcue.service

# Only after the new service is healthy do we update persistent configuration.
"$PYTHON_BIN" "$ROOT/scripts/configure.py" install --root "$ROOT"
TITLE_MODE_APPLIED=1
PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -c "from pathlib import Path; from tmuxcue.settings import apply_title_mode, set_source_dir, title_mode; set_source_dir(Path('$ROOT')); apply_title_mode(title_mode())"

# Apply four display options directly. Never source the complete tmux config.
if tmux list-sessions >/dev/null 2>&1; then
    tmux set-option -g set-titles on
    tmux set-option -g set-titles-string '#{?#{@agent_title},#{@agent_title},tmux #S:#I:#W}'
    tmux set-option -g window-status-format ' #{@agent_icon} #I:#W#{?window_flags,#{window_flags}, } '
    tmux set-option -g window-status-current-format ' #{@agent_icon} #I:#W#{?window_flags,#{window_flags}, } '
    tmux refresh-client -S >/dev/null 2>&1 || true
fi

# Install shell completions for new shells without modifying shell startup files.
mkdir -p "$HOME/.local/share/bash-completion/completions" \
    "$HOME/.local/share/zsh/site-functions" \
    "$HOME/.config/fish/completions"
PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -m tmuxcue __completion bash > "$TMP/tmuxcue.bash"
PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -m tmuxcue __completion zsh > "$TMP/_tmuxcue"
PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -m tmuxcue __completion fish > "$TMP/tmuxcue.fish"
install -m 644 "$TMP/tmuxcue.bash" "$HOME/.local/share/bash-completion/completions/tmuxcue"
install -m 644 "$TMP/_tmuxcue" "$HOME/.local/share/zsh/site-functions/_tmuxcue"
install -m 644 "$TMP/tmuxcue.fish" "$HOME/.config/fish/completions/tmuxcue.fish"

TMUX_PID_AFTER="$(pgrep -o -u "$(id -u)" tmux || true)"
PANES_AFTER="$(tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}:#{pane_pid}' 2>/dev/null | sort || true)"
AI_AFTER="$(PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -m tmuxcue __process-snapshot)"
[ "$TMUX_PID_BEFORE" = "$TMUX_PID_AFTER" ] \
    && [ "$PANES_BEFORE" = "$PANES_AFTER" ] \
    && [ "$AI_BEFORE" = "$AI_AFTER" ] || {
    echo "Safety check failed: a running terminal process changed during installation." >&2
    exit 1
}

# Configure server-side VS Code Remote titles only after process safety checks pass.
PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -m tmuxcue.vscode install
COMMITTED=1

# Re-emit each existing title. New tmux clients receive it automatically on attach.
if tmux list-sessions >/dev/null 2>&1; then
    while IFS= read -r window; do
        title="$(tmux show-option -w -v -t "$window" @agent_title 2>/dev/null || true)"
        [ -z "$title" ] || tmux set-option -w -t "$window" @agent_title "$title" >/dev/null 2>&1 || true
    done < <(tmux list-windows -a -F '#{session_name}:#{window_index}')
fi

echo "TmuxCue installed. Existing tmux and terminal AI processes were left untouched."
echo "Codex users: run /hooks once to review and trust the hooks."
