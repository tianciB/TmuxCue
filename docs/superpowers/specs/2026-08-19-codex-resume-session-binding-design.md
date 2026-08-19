# Codex Resume Session Binding Design

## Problem

TmuxCue identifies a Codex thread from the foreground process, working
directory, and Codex state database. An explicit command such as
`codex resume <uuid>` is exact, but interactive `codex resume` contains no
thread ID. The fallback currently favors a user thread created near the
foreground process start time.

That heuristic fails when an interactive resume selects an older thread while
another short-lived thread was created shortly before the new Codex process.
The Mage Flow pane was bound to the nearby short-lived thread instead of the
older resumed thread. Its selected runtime ended with `turn_aborted`, so the
cached aborted state overrode a live pane scan that correctly reported
`working`. The title and topic were also taken from the wrong thread.

The foreground Codex process already exposes a stronger read-only identity
signal: its open file descriptors include the active rollout JSONL and thread
writer lock. Both paths contain the exact thread UUID.

## Goals

- Bind interactive `codex resume` processes to the exact open user thread.
- Exclude open subagent rollouts from main-pane metadata selection.
- Preserve explicit UUID command-line matching as the highest priority.
- Preserve the existing creation-time heuristic when no exact process evidence
  is available.
- Prevent a runtime event that predates the current foreground Codex process
  from overriding a live working pane.
- Repair Mage Flow without sending input, signals, or process-control commands
  to any tmux pane or AI process.
- Publish the code version as patch version `0.4.5`.

## Non-goals

- Inspect prompt content to infer the selected thread.
- Select the globally most recently updated thread for a working directory.
- Change Codex or Claude hook configuration.
- Attach to, switch, restart, or terminate any tmux session or AI process.
- Track a subagent as the main pane title while the parent Codex process owns
  the terminal.

## Considered Approaches

### 1. Resolve the thread from open Codex file descriptors

Scan `/proc/<pid>/fd` symlink targets and accept UUIDs only from paths below
the configured Codex home:

- `sessions/**/rollout-...-<uuid>.jsonl`
- `thread-writer-locks/<uuid>.lock`

Intersect those UUIDs with user threads in the current working directory.
Select the most recently updated matching user thread if more than one exact
user-thread candidate remains.

This is the selected approach. It uses process-local, read-only evidence and
correctly excludes open subagent files through the existing
`thread_source='user'` database filter.

### 2. Select the most recently updated thread in the working directory

This repairs Mage Flow but can assign multiple simultaneous Codex panes in the
same repository to whichever thread was updated last. It is not process
specific and is rejected.

### 3. Ignore runtime metadata older than the process

This prevents the stale aborted state from hiding the running icon, but the
session ID, model, topic, hook association, and future runtime state remain
wrong. It is useful only as a defense-in-depth fallback.

## Detailed Design

### Open-session discovery

Add `MetadataResolver.codex_open_session_ids(pid)`:

1. Return an empty set for a missing or non-positive PID.
2. Require the process directory to be owned by the resolver UID.
3. Iterate the process FD directory under the injected `proc_root`.
4. Read symlink targets without opening or writing the target.
5. Ignore non-absolute paths, deleted suffixes, and targets outside
   `codex_home`.
6. Extract UUIDs only from session rollout JSONL or thread writer lock paths.
7. Return a set because a Codex process can keep parent and subagent rollouts
   open simultaneously.

### Metadata selection priority

`codex_metadata()` uses this order:

1. Explicit UUID present in the process command line.
2. Open-FD UUIDs that also match a user thread in the current working
   directory.
3. Existing process-start creation-time heuristic.

If multiple open-FD user-thread rows match, select the row with the greatest
`updated_at_ms`. Open subagent threads never enter the candidate set because
the database query already limits rows to `thread_source='user'`.

### Runtime safety fallback

In `IndicatorDaemon.raw_state()`, when:

- the row is a tmux `PaneRow`;
- the pane detector reports `working`; and
- a selected runtime `idle` or `aborted` event is older than
  `row.started_at`;

ignore that stale runtime event and retain the pane's working state. A valid
runtime completion emitted after the current process started remains
authoritative, preserving interrupted-turn and stale-title handling.

## Tests

Add metadata tests that create:

- a nearby wrong user thread;
- an older correct user thread;
- a fake Codex process FD pointing to the correct rollout;
- an open subagent rollout alongside the main rollout.

The exact user thread must win despite its old creation time. A target outside
the configured Codex home must be ignored.

Add a daemon test showing that an aborted runtime event older than the
foreground process cannot override a live working pane. Keep the existing test
showing that a newer aborted event overrides stale working UI.

Run the full test suite, shell syntax checks, and source CLI smoke checks.

## Live Safety Verification

Before installation, record:

- tmux server PID;
- every tmux pane PID;
- every running Codex and Claude PID.

Install the tested version, which restarts only `tmuxcue.service`. Require exact
before/after equality for all recorded process identities.

After a fresh scan, Mage Flow must report:

- the user thread exposed by its open rollout FD;
- runtime state `working`;
- visual state `running`;
- a non-placeholder topic from the resumed thread.

## Success Criteria

- All automated tests pass.
- Installed CLI reports `TmuxCue 0.4.5`.
- Mage Flow is bound to its open user rollout rather than the nearby aborted
  thread.
- Mage Flow displays a running icon while its pane is working.
- TmuxCue remains enabled and active without restart-loop errors.
- No tmux pane, Codex process, or Claude process changes PID during repair.
