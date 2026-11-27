# TmuxCue

TmuxCue shows the live status, model, and topic of Codex, Claude, and Agy CLI sessions in tmux windows.

## Status indicators

| Indicator | Meaning |
|---|---|
| ❓ | Waiting for approval or user input |
| 🌑 → 🌘 | Running |
| ✅ | Turn completed or was interrupted; waiting for the next prompt |

Example titles:

```text
🌒 [tmux] codex/gpt-5.6-sol: Analyze the codebase and add tests...
✅ [tmux] claude/claude-opus-5: Research image generation milestones...
✅ [tmux] agy/gemini-4-neon-xhigh: Analyze repository and cluster status...
```

TmuxCue manages tmux windows only; regular terminals outside tmux are left completely untouched.

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

## Codex session binding

Codex tmux panes do not borrow an old thread solely because it shares their
working directory. If no earlier binding path selects a thread, TmuxCue
keeps the pane unbound and uses its native subject or directory name instead
of another thread's model, topic, Goal, or hooks. Normal metadata refresh
binds the current thread when it becomes discoverable.

Command-line IDs, open rollout/writer-lock files, process logs, unique native
title matches, and the existing creation-time-proximity heuristic retain
their selection order. Proximity remains a heuristic, not an exact process
binding. Claude and Agy behavior are unchanged.

## Codex Goal mode

TmuxCue distinguishes the current Codex turn, detached background terminals,
and the durable Codex Goal:

- an exact bound Goal with status `active` keeps the running moon visible
  between continuation turns;
- `blocked` shows `❓`;
- `paused`, `complete`, `usage_limited`, and `budget_limited` show `✅`;
- a `N background terminal(s) running` message alone does not change state.

Goal state is read by exact thread ID from Codex's local `goals_*.sqlite`
database in read-only mode. TmuxCue queries only the status and update time; it
does not read or store the Goal objective, budget, or usage details.

Exact Goal state supplements the current pane rather than replacing current
native UI evidence. A visible approval dialog remains `❓`, and a pane with a
current working spinner remains running even if an older durable Goal is still
marked `blocked`. An idle pane can still be promoted by `goal_active` or
`goal_blocked`. Newer completion and abort runtime evidence can still clear a
stale working spinner.

If the Codex TUI is currently displaying a `/side` conversation, that side
conversation's visible pane state takes priority. Its current working UI shows
the running moon, a current approval dialog shows `❓`, and an idle side
conversation shows `✅`. While the side conversation is selected, TmuxCue
ignores the parent thread's Goal status, lifecycle hooks, and prompt grace.
Closing the side conversation or switching back restores the parent thread's
Goal behavior on the next scan.

Side context is detected only from the final 20 captured pane lines. TmuxCue
stores only a boolean result: it does not read subagent spawn edges or persist
side questions, history, labels, or thread IDs.

## Review Agy hooks

After installation, enter `/hooks` in Agy CLI (or inspect `~/.gemini/config/hooks.json`) to review the `tmuxcue` user hooks:

| Hook | Purpose | Expected command |
|---|---|---|
| `SessionStart` | Bind the Agy conversation and show the waiting state | `"$HOME/.local/bin/tmuxcue-hook" --agent agy --state done` |
| `PreInvocation` | Switch to the shared running indicator | `"$HOME/.local/bin/tmuxcue-hook" --agent agy --state running` |
| `Stop` | Show ✅ when the execution loop terminates | `"$HOME/.local/bin/tmuxcue-hook" --agent agy --state done` |

Agy hooks are observational only: they output `{}` and never block, deny, or
inject steps. Agy event files contain only the agent, conversation ID, state,
event, timestamp, and an exact PID/start-time/TTY binding. They do not store
prompts, responses, transcript paths, or tool arguments.

TmuxCue detects foreground `agy` processes running inside tmux panes (including
wrapper-launched binaries), binds each pane to its active conversation from
open `presence/*.lock` file descriptors (falling back to bound hooks or
`--conversation`/`--resume` flags), and detects live work, queued messages, and
approval prompts from the bounded Agy composer/status block plus waiting-step
checks in `~/.gemini/antigravity-cli`. Unrelated user hooks in
`~/.gemini/config/hooks.json` are preserved and backed up. Legacy Gemini CLI
hooks previously added by TmuxCue in `~/.gemini/settings.json` are cleaned up
automatically during installation or uninstall.

Claude Code sessions that use custom API backends may emit an intermediate
`Stop` event before all work has finished. For tmux panes, TmuxCue checks
both the bound Claude session's live `busy` status and the current status
line immediately above the input composer. Progress with an elapsed/token
meter, or an explicit wait for one or more background agents, keeps the
running moon active even when the main turn temporarily reports idle.
A real approval dialog still takes priority and shows `❓`.

Only a current, bounded footer is used: old output, quoted status text,
completion summaries, and the static `✳` title do not imply ongoing work.
A genuinely idle pane returns to `✅` without an added completion delay.
Claude CLI sessions using custom model backends follow these Claude rules.

Uninstalling with `tmuxcue uninstall` removes only TmuxCue services, hooks, display options, and installed files. It restores any previous native terminal-title and VS Code Remote settings and does not close or restart any tmux pane or terminal AI process.

## Security

- Refuses installation as `root` and never requires `sudo`.
- Backs up tmux, Codex, Claude, Agy, and existing VS Code Remote configuration before editing.
- Never runs `kill-session`, `kill-window`, `send-keys`, `attach`, `respawn`, or sends signals to AI processes.
- Verifies that the tmux server, every tmux pane PID, and detected Codex, Claude, and Agy process identities remain unchanged.
- Uses no `#(...)` shell commands in tmux title formats.
- Never writes to TTY devices or modifies terminals outside tmux.
- Stores hook events with `0600` permissions.
- Runs the user service with `NoNewPrivileges` and `UMask=0077`.

## License

MIT License.
