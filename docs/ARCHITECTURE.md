# Architecture

```text
Codex / Claude / Gemini lifecycle hooks ─┐
Codex / Claude / Gemini local metadata  ─┼─> one user-level Python service
native tmux pane detector               ─┤              │
foreground terminal detector           ─┘              ├─ tmux window options
                                                        └─ sanitized OSC terminal title
```

## Detection

TmuxCue uses native operating-system and tmux interfaces only:

- `tmux list-panes` for pane identity, process, cwd, title, and TTY;
- process inspection for foreground Codex, Claude, and official Node-based
  Gemini CLI processes;
- `tmux capture-pane` for a short, bounded tmux screen tail;
- Codex, Claude, and Gemini lifecycle hooks for prompt, approval, completion,
  and session events;
- local Codex, Claude, and Gemini metadata for model and topic labels;
- read-only Codex, Claude, and bound Gemini session records for direct
  terminals.
- exact-thread Codex Goal state from the read-only `goals_*.sqlite`
  `thread_goals` table.
- the Codex TUI side-context label, matched only within the final 20 captured
  pane lines.

Direct-terminal discovery excludes every TTY owned by a tmux pane.

Gemini CLI is identified from its exact official Node entry-point command
rather than from a generic `node` process or a loose `gemini` substring.
Gemini hook events bind a row by PID, process start time, and TTY. The bound
session UUID selects one JSONL transcript. A legacy fallback is accepted only
when exactly one transcript matches the working directory and process
incarnation.

## Output safety

Tmux formats contain no shell interpolation and never use `#(...)`. The updater
runs outside tmux's redraw path and writes only window options.

For a regular terminal, TmuxCue writes one sanitized OSC title sequence. Before
every write it verifies that:

- the target is a numeric `/dev/pts/*` character device;
- the device and AI process belong to the current user;
- `/proc/<pid>/fd/0` still points to the same TTY;
- the opened file descriptor still matches the validated device.

No input bytes, signals, attach commands, or process-control commands are sent.

## State model

- `approval`: a lifecycle hook or tmux UI indicates an approval request;
- `running`: a prompt was submitted, a lifecycle event is active, or runtime
  metadata indicates active work;
- `done`: the turn completed or was interrupted, and the agent is waiting for the next prompt.

All three agents use the same final visual mapping. Gemini native titles
(`✋ Action Required`, `✦ Working`, and `◇ Ready`) are raw observations only;
TmuxCue still renders `❓`, the rotating tmux moon, or `✅`.

Gemini's `✋ Action Required` title is ambiguous: the CLI also uses it when an
interactive shell has produced output and is waiting for focus while work may
continue automatically. TmuxCue classifies that title as approval only when
the bounded screen tail contains a current confirmation dialog. Without that
corroboration it is live working evidence. A bound `ToolPermission` hook
remains independent high-confidence approval evidence.

For Claude tmux panes, a bound local session whose live status is `busy` is
authoritative working evidence even when the native title is a static derived
session name. Each current read of that busy state can supersede an older
intermediate `Stop` event from a custom model backend. A blocked pane remains
approval, and an idle runtime state remains done. The static `✳` title prefix
is not independently treated as a spinner.

Codex Goal state is distinct from turn state and detached background terminal
state. An exact Goal status of `active` is current working evidence even while
the thread is between continuation turns. `blocked` maps to approval;
`paused`, `complete`, `usage_limited`, and `budget_limited` map to done.
Ordinary rollout `task_started` evidence cannot promote an idle pane unless
the native pane also shows work, which prevents stale rollout events from
reviving completed non-Goal sessions.

A current Codex tmux observation is authoritative over an older blocked Goal.
A visible approval dialog remains blocked, while a current native working
spinner remains working even if the durable Goal is still `blocked` from an
earlier stall. Exact Goal state continues to supplement an idle pane. This
exception is limited to `goal_blocked`; ordinary runtime completion and abort
events still pass through the existing freshness rules and can clear a stale
spinner.

When a Codex pane displays a `/side` conversation, its bounded live UI is the
authoritative state source. The pane detector's working, blocked, or idle
result bypasses the parent thread's Goal/runtime snapshot, lifecycle hook, and
prompt-grace state. The side-active boolean is part of both row identity and
metadata-source identity, so closing the side conversation immediately drops
cached state and restores normal parent-thread resolution.

Side detection does not query `thread_spawn_edges`; those records describe
subagents rather than the transient TUI side conversation. TmuxCue stores only
the side-active boolean and does not persist side text, history, labels, or
thread IDs.

## Installation layout

```text
~/.local/share/tmuxcue/current
~/.local/bin/tmuxcue
~/.local/bin/tmuxcue-hook
~/.local/bin/tmuxcue-service
~/.config/systemd/user/tmuxcue.service
```

Install, update, and uninstall scripts verify that the tmux server, all pane
PIDs, and all detected Codex, Claude, and Gemini process identities remain
unchanged. Gemini identities include every recognized Node layer and its
process start time and TTY.
