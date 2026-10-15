# TmuxCue

TmuxCue shows the live status, model, and topic of Codex, Claude, and Gemini CLI sessions in tmux windows and, optionally, regular terminal tabs.

## Status indicators

| Indicator | Meaning |
|---|---|
| ❓ | Waiting for approval or user input |
| 🌑 → 🌘 in tmux; stable 🌒 in regular terminals when enabled | Running |
| ✅ | Turn completed or was interrupted; waiting for the next prompt |

Example titles:

```text
🌒 [tmux] codex/gpt-5.6-sol: Analyze the codebase and add tests...
✅ [term] claude/claude-opus-5: Research image generation milestones...
✅ [tmux] gemini/gemini-2.5-pro: Add lifecycle supervision...
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

TmuxCue preserves native Codex, Claude, and Gemini terminal titles by default. Tmux integration remains enabled, but regular-terminal titles are not changed:

```bash
tmuxcue config terminal-title native
```

To let TmuxCue own regular-terminal titles and prevent competing running indicators, opt in with:

```bash
tmuxcue config terminal-title override
```

Override mode sets Codex `tui.terminal_title = []`, Claude
`CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1`, and Gemini
`ui.dynamicWindowTitle=false` plus `ui.showStatusInTitle=false`. Previous
values are preserved for uninstall or native mode. Changes apply without
restarting an existing process; a CLI version that does not reload settings
dynamically picks them up in a newly started session.

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

## Review Gemini hooks

After installation, enter `/hooks panel` in Gemini CLI and review the five
TmuxCue user hooks:

| Hook | Purpose | Expected command |
|---|---|---|
| `SessionStart` | Bind the Gemini session and show the waiting state | `"$HOME/.local/bin/tmuxcue-hook" --agent gemini --state done` |
| `BeforeAgent` | Switch to the shared running indicator | `"$HOME/.local/bin/tmuxcue-hook" --agent gemini --state running` |
| `Notification` with matcher `ToolPermission` | Show ❓ for a tool permission request | `"$HOME/.local/bin/tmuxcue-hook" --agent gemini --state approval` |
| `AfterAgent` | Show ✅ when the turn finishes | `"$HOME/.local/bin/tmuxcue-hook" --agent gemini --state done` |
| `SessionEnd` | Remove ended-session state | `"$HOME/.local/bin/tmuxcue-hook" --agent gemini --state ended` |

The `ToolPermission` hook is observational only: it cannot approve, deny, or
modify Gemini's permission flow. Gemini event files contain the agent, session
ID, state, event, timestamp, and an exact PID/start-time/TTY binding. They do
not contain prompts, responses, transcript paths, working directories,
notification messages, tool arguments, or permission details.

Gemini also uses its native `✋ Action Required` title as an interactive-shell
focus hint while a command may still be running automatically. TmuxCue shows
`❓` only when a current confirmation dialog is visible near the bottom of the
pane or a bound `ToolPermission` hook reports a real approval request.
Otherwise that title is treated as running work.

TmuxCue configures the default `~/.gemini/settings.json` and safe custom user
settings discovered from running Gemini processes that set
`GEMINI_CLI_HOME`. Existing settings and unrelated hooks are preserved and
backed up. Repository-local `.gemini/settings.json` files are never modified.

Gemini model and topic labels are read from only the session bound by PID,
process start time, TTY, and session ID. If a pre-hook session has multiple
possible transcripts in the same working directory, TmuxCue leaves it
unbound instead of borrowing another session's state or topic.

Uninstalling with `tmuxcue uninstall` removes only TmuxCue services, hooks, display options, and installed files. It restores the previous native terminal-title and VS Code Remote settings and does not close or restart any tmux pane or terminal AI process.

## Security

- Refuses installation as `root` and never requires `sudo`.
- Backs up tmux, Codex, Claude, Gemini, and existing VS Code Remote configuration before editing.
- Never runs `kill-session`, `kill-window`, `send-keys`, `attach`, `respawn`, or sends signals to AI processes.
- Verifies that the tmux server, every tmux pane PID, and detected Codex, Claude, and Node-based Gemini process identities remain unchanged.
- Uses no `#(...)` shell commands in tmux title formats.
- Writes only a sanitized OSC title sequence to a foreground TTY owned by the current user.
- Verifies `/proc/<pid>/fd/0` still points to that TTY before every title write.
- Excludes every TTY owned by a tmux pane from direct-terminal updates.
- Stores hook events with `0600` permissions.
- Runs the user service with `NoNewPrivileges` and `UMask=0077`.

## License

MIT License.
