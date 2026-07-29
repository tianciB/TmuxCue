# TmuxCue

TmuxCue shows the live status, model, and topic of Codex and Claude sessions in tmux windows and, optionally, regular terminal tabs.

## Status indicators

| Indicator | Meaning |
|---|---|
| ❓ | Waiting for approval or user input |
| 🌑 → 🌘 in tmux; stable 🌒 in regular terminals when enabled | Running |
| ✅ | Turn completed; waiting for the next prompt |

Example titles:

```text
🌒 [tmux] codex/gpt-5.6-sol: Analyze the codebase and add tests...
✅ [term] claude/claude-opus-5: Research image generation milestones...
```

`[tmux]` identifies a tmux window. `[term]` identifies a regular terminal when terminal-title override mode is enabled.

## VS Code Remote

TmuxCue automatically configures the server-side VS Code Remote title template:

```json
{
  "terminal.integrated.tabs.title": "${sequence}",
  "terminal.integrated.tabs.description": ""
}
```

The setting is stored on the server, so a new VS Code Remote SSH connection from another computer loads TmuxCue titles without per-client setup or a manual refresh. Existing Remote settings are merged and backed up; uninstall restores the previous values when they are still managed by TmuxCue.

## Install

```bash
git clone https://github.com/tianciB/TmuxCue.git
cd TmuxCue
./install.sh
```

## Commands

```bash
tmuxcue update
tmuxcue version
tmuxcue uninstall
tmuxcue help
```

`tmuxcue update` requires a clean Git worktree, uses `git pull --ff-only`, and runs the same safety checks as installation. Bash, Zsh, and Fish completion files are installed automatically; open a new shell to enable Tab completion.

## Terminal title mode

TmuxCue preserves native Codex and Claude terminal titles by default. Tmux integration remains enabled, but regular-terminal titles are not changed:

```bash
tmuxcue config terminal-title native
```

To let TmuxCue own regular-terminal titles and prevent competing running indicators, opt in with:

```bash
tmuxcue config terminal-title override
```

Override mode sets Codex `tui.terminal_title = []` and Claude `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1`, while preserving the previous values for uninstall or native mode. It applies only to newly started Codex and Claude sessions; TmuxCue never restarts an existing process.

## Review Codex hooks

After installation, enter `/hooks` in Codex. The page always lists every supported lifecycle event; rows with `Installed 0` are built-in empty entries and require no action.

TmuxCue installs and requires trust for exactly these three hooks:

| Hook | Purpose | Expected command |
|---|---|---|
| `UserPromptSubmit` | Switch to running immediately after prompt submission | `"$HOME/.local/bin/tmuxcue-hook" --agent codex --state running` |
| `PermissionRequest` | Show ❓ when Codex requests approval | `"$HOME/.local/bin/tmuxcue-hook" --agent codex --state approval` |
| `Stop` | Show ✅ when the turn finishes | `"$HOME/.local/bin/tmuxcue-hook" --agent codex --state done` |

Before trusting them, verify that:

- only these three events have `Installed 1`;
- every other event has `Installed 0`;
- each command exactly matches the table above;
- no unexpected path, argument, or additional installed event is present.

The hooks only record the agent, session ID, state, event name, and timestamp. They do not approve permissions, execute requested tools, or store prompt content.

Uninstalling with `tmuxcue uninstall` removes only TmuxCue services, hooks, display options, and installed files. It restores the previous native terminal-title and VS Code Remote settings and does not close or restart any tmux pane or terminal AI process.

## Security

- Refuses installation as `root` and never requires `sudo`.
- Backs up tmux, Codex, Claude, and existing VS Code Remote configuration before editing.
- Never runs `kill-session`, `kill-window`, `send-keys`, `attach`, `respawn`, or sends signals to AI processes.
- Verifies that the tmux server, every tmux pane PID, and every direct-terminal AI PID remain unchanged.
- Uses no `#(...)` shell commands in tmux title formats.
- Writes only a sanitized OSC title sequence to a foreground TTY owned by the current user.
- Verifies `/proc/<pid>/fd/0` still points to that TTY before every title write.
- Excludes every TTY owned by a tmux pane from direct-terminal updates.
- Stores hook events with `0600` permissions.
- Runs the user service with `NoNewPrivileges` and `UMask=0077`.

## License

MIT License.
