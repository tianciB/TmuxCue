# Codex Goal Runtime State Design

**Date:** 2026-10-16
**Status:** Approved
**Target release:** v0.5.3

## Problem

TmuxCue can show a Codex pane as completed while its durable Codex Goal remains
active.

TmuxCue currently supervises turn-level evidence:

- native pane working/approval UI;
- lifecycle hooks;
- `task_started`, `task_complete`, and `turn_aborted` rollout events.

Codex Goal mode is higher-level. An active Goal can remain active between
continuation turns while the thread is temporarily idle. The affected pane
has an exact bound Codex thread whose durable Goal status is `active`, but its
current turn has no recent working event, so TmuxCue renders `✅`.

Goal state is stored in Codex's local `goals_*.sqlite` database in the
`thread_goals` table. Parsing Goal tool output from the rollout is less
reliable: long rollouts can move the last Goal result outside TmuxCue's bounded
tail, and tool output is not the durable source of truth.

## Goals

- Read Codex Goal status from the newest local `goals_*.sqlite` database in
  read-only mode.
- Bind Goal state by exact Codex thread/session ID.
- Display an active Goal as running even between continuation turns.
- Display a blocked Goal as requiring user attention.
- Display paused, complete, usage-limited, and budget-limited Goals as done.
- Preserve live pane approval and working evidence.
- Preserve behavior for Codex threads that have no Goal.
- Preserve Claude and Gemini behavior.
- Deploy, push, tag, and release without sending input or signals to any
  existing process.

## Non-goals

- Creating, pausing, resuming, blocking, or completing a Codex Goal.
- Modifying Codex Goal budgets, continuation limits, or usage limits.
- Parsing or storing the Goal objective.
- Treating background terminal processes as Goal state.
- Scanning unrelated threads or borrowing another thread's Goal.

## Design

### 1. Goal database discovery

Add a metadata helper that finds the newest `CODEX_HOME/goals_*.sqlite` file.
The helper must tolerate:

- an absent database;
- a missing `thread_goals` table;
- schema changes;
- a locked or unreadable database;
- malformed status or timestamps.

Open the database with SQLite URI `mode=ro`. No migration, write, WAL
checkpoint, or schema command is allowed.

### 2. Exact Goal lookup

Query only:

```sql
select status, updated_at_ms
from thread_goals
where thread_id = ?
```

The parameter is the exact `Metadata.session_id` already bound to the pane or
terminal. A row from another thread is never considered.

Return a runtime snapshot with the Goal update timestamp converted from
milliseconds to seconds.

### 3. Runtime state mapping

Map durable Goal states to internal runtime states:

| Codex Goal status | Internal runtime state |
|---|---|
| `active` | `goal_active` |
| `blocked` | `goal_blocked` |
| `paused` | `idle` |
| `complete` | `idle` |
| `usage_limited` | `idle` |
| `budget_limited` | `idle` |

An absent or unknown Goal status returns no Goal snapshot and falls back to
the existing rollout task-state parser.

When a valid Goal row exists, it is the higher-level durable state. Live pane
evidence can still override a terminal Goal state:

- a pane that visibly works remains running even if a newly paused/completed
  Goal snapshot is slightly older than the scan;
- a blocked pane remains approval;
- an active Goal remains running between turns.

### 4. Service integration

Idle-looking Codex panes must reach runtime-state resolution so TmuxCue can
discover `goal_active` and `goal_blocked`.

Limit promotion for an idle Codex pane:

- `goal_active` → raw `working`;
- `goal_blocked` → raw `blocked`;
- any ordinary rollout `working` result does not promote an idle Codex pane;
- `idle`, `aborted`, or no runtime state keeps the existing detector result.

This restriction prevents a stale rollout `task_started` event from reviving a
normal completed Codex pane.

Each current read of `goal_active` or `goal_blocked` is treated as fresh
runtime evidence:

- active can supersede an older `Stop` or `task_complete`;
- blocked can supersede an older completion event and resolve to `❓`.

For direct terminals, Goal states use the same mapping, with the existing
stable direct-terminal running icon.

### 5. Background terminals

Do not infer Goal activity from `N background terminal(s) running`. A detached
server or monitor can outlive the Codex turn and Goal. The exact Goal database
row is the authoritative source.

### 6. Privacy and safety

Read only `thread_id`, `status`, and `updated_at_ms`. Do not read, log, or
persist the Goal objective, budget, token usage, or time usage.

All install/update/uninstall process-identity checks remain unchanged.

### 7. Documentation and release

Document the distinction between:

- current Codex turn state;
- detached background terminal state;
- durable Codex Goal state.

Release the change as v0.5.3.

## Testing

Add regression coverage for:

- exact active Goal lookup → `goal_active`;
- exact blocked Goal lookup → `goal_blocked`;
- paused, complete, usage-limited, and budget-limited lookup → idle;
- unknown status and absent database → existing rollout fallback;
- another thread's active Goal is ignored;
- idle Codex pane + `goal_active` → running;
- idle Codex pane + `goal_blocked` → approval;
- idle Codex pane + ordinary rollout working → remains idle;
- active Goal supersedes an older done hook;
- live blocked pane remains approval;
- no-Goal Codex behavior remains unchanged;
- Claude and Gemini regressions remain green;
- full unit-test discovery.

## Acceptance criteria

1. The affected Codex pane displays the running moon while its exact durable
   Goal status is active.
2. Paused or completed Goals display `✅`.
3. Blocked Goals display `❓`.
4. Background terminal count alone does not change state.
5. Threads without a Goal preserve current behavior.
6. All tests pass with no new runtime dependency.
7. Local deployment leaves the tmux server, every pane identity, and every
   detected AI process identity unchanged.
8. v0.5.3 is pushed to `main`, tagged, and published as the latest GitHub
   Release.
