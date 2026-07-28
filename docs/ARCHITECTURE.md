# Architecture

```text
Codex / Claude lifecycle hooks ─┐
Codex / Claude local metadata  ─┼─> one user-level Python service
native tmux pane detector      ─┤              │
foreground terminal detector  ─┘              ├─ tmux window options
                                               └─ sanitized OSC terminal title
```

## Detection

TmuxCue uses native operating-system and tmux interfaces only:

- `tmux list-panes` for pane identity, process, cwd, title, and TTY;
- process inspection for foreground Codex and Claude processes;
- `tmux capture-pane` for a short, bounded tmux screen tail;
- Codex and Claude lifecycle hooks for prompt, approval, and stop events;
- local Codex and Claude metadata for model and topic labels;
- read-only Codex transcript and Claude session state for direct terminals.

Direct-terminal discovery excludes every TTY owned by a tmux pane.

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
- `done`: the interactive agent is waiting for the next prompt.

## Installation layout

```text
~/.local/share/tmuxcue/current
~/.local/bin/tmuxcue
~/.local/bin/tmuxcue-hook
~/.local/bin/tmuxcue-service
~/.config/systemd/user/tmuxcue.service
```

Install and update scripts verify that the tmux server, all pane PIDs, and all
direct-terminal Codex/Claude PIDs remain unchanged.
