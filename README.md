# TmuxCue

TmuxCue shows the live status, model, and topic of Codex and Claude sessions in tmux windows and regular terminal tabs.

## Status indicators

| Indicator | Meaning |
|---|---|
| ❓ | Waiting for approval or user input |
| 🌑 → 🌘 | Running |
| ✅ | Turn completed; waiting for the next prompt |

Example titles:

```text
🌒 codex/gpt-5.6-sol: Analyze the codebase and add tests...
✅ claude/claude-opus-5: Research image generation milestones...
```

For VS Code terminal tabs, use:

```json
{
  "terminal.integrated.tabs.title": "${sequence}",
  "terminal.integrated.tabs.description": ""
}
```

## Install

```bash
git clone https://github.com/tianciB/TmuxCue.git
cd TmuxCue
./install.sh
```

## Update

```bash
./update.sh
```

The updater requires a clean Git worktree, uses `git pull --ff-only`, and runs the same safety checks as installation.

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

## Uninstall

```bash
./uninstall.sh
```

Uninstalling removes only TmuxCue services, hooks, display options, and installed files. It does not close or restart any tmux pane or terminal AI process.

## Security

- Refuses installation as `root` and never requires `sudo`.
- Backs up tmux, Codex, and Claude configuration before editing.
- Never runs `kill-session`, `kill-window`, `send-keys`, `attach`, `respawn`, or sends signals to AI processes.
- Verifies that the tmux server, every tmux pane PID, and every direct-terminal AI PID remain unchanged.
- Uses no `#(...)` shell commands in tmux title formats.
- Writes only a sanitized OSC title sequence to a foreground TTY owned by the current user.
- Verifies `/proc/<pid>/fd/0` still points to that TTY before every title write.
- Excludes every TTY owned by a tmux pane from direct-terminal updates.
- Stores hook events with `0600` permissions.
- Runs the user service with `NoNewPrivileges` and `UMask=0077`.

Set `TMUXCUE_TERMINAL_TITLES=0` in the user service environment to disable regular-terminal title updates while keeping tmux integration enabled.

## License

MIT License.
