# Architecture

```text
Codex / Claude lifecycle hooks ─┐
Codex / Claude local metadata  ─┼─> one external Python service
native tmux pane detector      ─┘              │
                                               ├─ @agent_state
                                               ├─ @agent_icon
                                               └─ @agent_title
                                                        │
                                                 pure tmux formats
```

## Independent detector

The repository implements its own detector using only native operating-system
and tmux interfaces:

- `tmux list-panes` for pane identity, process, cwd, title, and tty;
- process inspection for Codex and Claude foreground processes;
- `tmux capture-pane` for a short, bounded screen tail;
- lifecycle hooks for prompt submission, approvals, stop, and session events;
- local Codex and Claude metadata for model and topic labels.

No external Agent monitoring project or binary is required.

## Safety invariant

The tmux formats contain no shell interpolation and never use `#(...)`. The
updater runs as one systemd user service outside tmux's redraw path. This avoids
recursive tmux client creation and keeps title rendering independent from
metadata or detector failures.

## State model

- `approval`: a lifecycle hook or terminal UI indicates an approval request.
- `running`: a prompt was submitted or the pane title/screen indicates work.
- `done`: the interactive agent is idle and waiting for the next prompt.

Codex history is also tailed incrementally as a fallback. Prompt submission can
therefore change the title within the daemon loop interval even before the TUI
redraws.

## Metadata model

- Codex: exact thread ID from `codex resume UUID` when available, otherwise
  cwd/start-time matching; model and optional name from `state_*.sqlite`; latest
  substantive prompt from `history.jsonl`.
- Claude: live session metadata, transcript model field, and latest human input
  from `history.jsonl`.

Generic acknowledgements and slash commands are skipped as title topics.
