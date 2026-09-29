# Codex Live Working State and PID Session Binding Design

**Date:** 2026-09-29
**Status:** Implemented and locally deployed
**Target release:** v0.4.6

## Problem

TmuxCue can show a Codex tmux window as completed even while that window is
still visibly and actively thinking.

The live `codex-1` incident exposed two coupled causes:

1. Codex emitted `task_complete` and `Stop` at `2026-09-29 09:01:10 UTC`,
   while the same pane continued to show changing reasoning text and an
   animated braille working title after `09:08 UTC`. TmuxCue currently lets
   the runtime `idle` result replace the newer tmux `working` observation.
2. Current Codex processes do not always keep their rollout JSONL or
   thread-writer lock open. When several resumed Codex processes share one
   working directory, TmuxCue falls back to the most recently updated thread
   and can bind multiple panes to the same session. This causes topics, hook
   events, and runtime state to leak between `codex-1`, `codex-2`, and
   `codex-3`.

Codex's local `logs_2.sqlite` records a process identity in the form
`pid:<PID>:<UUID>` together with the thread ID handled by that process. In the
observed incident it correctly distinguishes all affected Codex panes.
However, Codex rotates per-process log rows, so a long-running TUI can retain
its process identity after the older row containing `thread_id` has expired.
The native tmux pane title still carries the exact user-assigned thread name
in that case.

## Goals

- Keep a tmux pane in the running state while a newer live pane scan contains
  explicit Codex working evidence.
- Prevent an older `task_complete` or `Stop` event from overriding that newer
  working evidence.
- Bind resumed Codex processes in the same working directory to their actual
  top-level user threads.
- Retain exact same-cwd binding after PID-log thread rows rotate away.
- Preserve existing behavior for approvals, explicit aborts, direct session
  IDs, open rollout files, open writer locks, and safe fallback behavior.
- Deploy without sending input or signals to Codex, tmux panes, Docker
  containers, training jobs, or unrelated services.

## Non-goals

- Changing Codex hook configuration or Codex itself.
- Detecting whether Codex's own TUI should have emitted a completion event.
- Managing, interrupting, restarting, or attaching to an agent process.
- Replacing the existing thread database or cwd-based fallback.
- Adding a dependency outside the Python standard library.

## Design

### 1. PID-backed Codex session binding

Add a metadata resolver helper that reads `CODEX_HOME/logs_2.sqlite` in
read-only mode and looks for the newest non-empty `thread_id` whose
`process_uuid` belongs to the requested operating-system PID.

The process identity match is exact on the `pid:<PID>:` prefix. Results from
another PID must never be considered.

The resulting thread ID is only accepted if it is present among the existing
candidate rows selected from the current Codex state database:

- `cwd` exactly matches the pane or terminal cwd;
- `archived=0`;
- `thread_source='user'`.

The Codex thread selection order becomes:

1. Session UUID explicitly present in the Codex command line.
2. A user thread whose rollout JSONL or thread-writer lock is open by the
   process.
3. A user thread associated with the process PID in `logs_2.sqlite`.
4. A user thread whose `name` uniquely and exactly matches the native pane
   title after removing one leading braille spinner and one trailing
   ` | repository` suffix.
5. A thread created close to the process start time.
6. The existing eligible-most-recent cwd fallback.

An absent database, missing table, schema mismatch, locked database, invalid
thread ID, or any SQLite/read error produces no PID binding and falls through
to the existing selection logic. It must not remove the pane from TmuxCue or
change the agent process.

The open-file binding remains ahead of log binding because it is already an
exact, direct association and requires no additional database query.

The native-title match is accepted only when exactly one existing candidate
row has that name. Empty, generic, truncated, or duplicate titles do not
override the existing heuristics. Direct-terminal rows without a native title
continue through the remaining selection order unchanged.

### 2. Newer live working evidence wins over completed runtime state

Tmux detection remains responsible for recognizing explicit working evidence,
including:

- an animated braille prefix in the native pane title;
- a current `Esc to interrupt` or `Ctrl+C to interrupt` status near the
  bottom of the pane.

When a pane scan reports `working`, compare the scan timestamp with the
runtime event timestamp:

- If runtime reports `idle` from `task_complete`, but the tmux scan is newer,
  return `working` and record the scan time as the observation time.
- If the completion event is newer than the most recent scan, completion may
  temporarily win until the next scan.
- A runtime `aborted` result retains its current authoritative behavior. This
  avoids treating a residual working title as active work after an explicit
  interruption.
- Runtime `working` behavior remains unchanged.

This ordering also makes an older `Stop` hook unable to turn the newer
working observation into `done`. A newer `PermissionRequest` can still
override a cached working scan immediately; a subsequent pane scan can clear
it when the approval UI is no longer present.

### 3. No process-control changes

The implementation only adds read-only metadata inspection and changes state
selection. It must not add any use of:

- `tmux send-keys`, `attach-session`, `kill-window`, `kill-session`, or
  `respawn-pane`;
- `os.kill`, `pkill`, `kill`, or signals to agent processes;
- writes to Codex databases or rollout files.

## Data flow

For each detected Codex pane:

1. Discover the foreground Codex PID from the pane TTY.
2. Extract an exact thread-name hint from the native pane title.
3. Resolve metadata using command-line, open-file, PID-log, unique native
   title, then existing heuristic binding.
4. Read the bound thread's runtime snapshot.
5. Compare runtime event time with the latest tmux scan time.
6. Merge the resulting observation with prompt and hook events.
7. Update only the TmuxCue tmux window options.

## Testing

### Metadata tests

- A PID-log association selects the correct user thread when two or more
  active threads share the same cwd.
- A newer log row for another PID is ignored.
- Open rollout and writer-lock bindings continue to outrank PID-log binding.
- A logged thread outside the candidate cwd/user-thread set is rejected.
- Missing, malformed, or unreadable `logs_2.sqlite` safely uses the existing
  fallback.
- A unique exact native-title name binds the correct thread when the current
  PID-log process identity has no retained `thread_id`.
- Duplicate or unmatched native-title names do not override existing
  selection rules.

### State tests

- A tmux `working` scan newer than runtime `task_complete` resolves to
  `running`.
- A completion event newer than the cached scan can still resolve to `done`.
- A later fresh working scan supersedes an older `Stop` hook.
- `turn_aborted` retains its existing behavior.
- Approval precedence and stale-approval clearing continue to pass.

### Regression tests

- Run the complete repository test suite.
- Keep installation-safety and privacy tests passing.
- Confirm no test introduces process-control commands or writes to Codex
  runtime data.

## Deployment and live verification

After all tests pass:

1. Record every tmux pane PID and every foreground Codex PID.
2. Install the tested repository revision using TmuxCue's existing safe
   installation path.
3. Restart only `tmuxcue.service`.
4. Re-record and compare all tmux pane and Codex PIDs; they must be unchanged.
5. Verify that `codex-1`, `codex-2`, and `codex-3` each show their own topic
   and session-derived state.
6. Verify an actively working pane remains running after an older completion
   event.
7. Inspect the TmuxCue journal for errors without attaching to or sending
   input to any agent pane.

If PID preservation fails or live state becomes less accurate, stop the
TmuxCue deployment verification and restore the previously installed TmuxCue
revision. Do not manipulate the affected agent sessions.

## Success criteria

- The reproduced `codex-1` condition no longer displays `✅` while a newer
  tmux scan still detects working activity.
- Same-cwd Codex panes resolve to their PID-associated top-level user threads
  or, after log rotation, their uniquely name-matched top-level user threads,
  instead of sharing the most recently updated thread.
- Existing approval, abort, completion, privacy, and installation-safety
  behavior remains covered and passing.
- Deployment changes only TmuxCue and leaves all other process and pane PIDs
  unchanged.
