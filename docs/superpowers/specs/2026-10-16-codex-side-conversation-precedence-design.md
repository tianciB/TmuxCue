# Codex Side Conversation Precedence Design

**Date:** 2026-10-16
**Status:** Implemented, deployed, and released
**Target release:** v0.5.4

## Problem

TmuxCue v0.5.3 correctly supervises a durable Codex Goal on the main thread,
but that Goal can incorrectly override the state of a currently displayed
Codex `/side` conversation.

Codex side conversations are transient, ephemeral forks. They are held by the
TUI and are not ordinary durable Goal rows. The TUI displays an explicit
context label while a side conversation is selected:

- `Side from main thread ...`;
- `Side from parent thread (...) ...`.

When the user switches back to the parent, the side label is removed or
replaced by a hint for reopening the side conversation.

TmuxCue currently binds metadata and runtime state to the durable main thread.
If the main thread has an active Goal, `goal_active` forces the running state
even when the currently displayed side conversation is idle, done, or waiting
for approval.

## Goals

- Detect when the currently displayed Codex pane is a side conversation.
- While the side conversation is displayed, derive state only from the side
  pane's current UI.
- Prevent the parent thread's Goal, hook, and prompt state from overriding the
  side conversation.
- Restore the parent thread's durable Goal state immediately after switching
  back or closing the side conversation.
- Preserve metadata labels and exact main-thread session binding.
- Preserve subagent, Claude, Gemini, and direct-terminal behavior.
- Deploy, push, tag, and release without sending input or signals to any
  existing process.

## Non-goals

- Persisting or reconstructing ephemeral side-thread history.
- Treating subagent `thread_spawn_edges` as side conversations.
- Connecting to the Codex app-server socket.
- Switching, closing, interrupting, or submitting input to a side
  conversation.
- Changing Codex's side-conversation behavior.

## Design

### 1. Side-context detection

Add a bounded screen detector:

```text
Side from main thread
Side from parent thread
```

Match only within the final 20 pane lines. Require the line to begin with
`Side from main thread` or `Side from parent thread`, ignoring leading
whitespace and case.

Do not treat the parent-view hint ending in `for side` as an active side
conversation.

### 2. Pane model

Add `side_active: bool = False` to `PaneRow`.

`TmuxDetector.scan()` sets the field from the current bounded pane capture.
The default preserves every existing constructor and non-Codex row.

Include `side_active` in daemon row and metadata-source identity keys so a
main/side switch invalidates cached state immediately.

### 3. Side state precedence

For a tmux `PaneRow` with `agent=codex` and `side_active=true`:

- raw state is exactly the detector's current pane state;
- do not poll or apply the parent thread's runtime or Goal state;
- do not apply the parent thread's lifecycle hook event;
- do not apply the parent thread's prompt-grace state.

The detector already maps the currently displayed UI:

- current working title/status → working;
- current approval dialog → blocked;
- no live work/approval evidence → idle.

This makes the side conversation's visible state authoritative.

### 4. Return to parent

When the active side label disappears:

- `side_active` changes from true to false;
- the row identity changes and cached raw/runtime/semantic state is dropped;
- normal metadata and hook resolution resumes;
- an active main Goal returns to running;
- a blocked main Goal returns to approval;
- a paused/completed main Goal returns to done.

### 5. Parent activity while side is displayed

Codex may continue the parent thread while the side conversation is open and
may surface parent status in the side context label. The user-selected side
conversation remains authoritative for TmuxCue. Parent status text in the
label must not change the side state.

### 6. Subagents

Do not query `thread_spawn_edges` for side state. Those rows describe spawned
subagent threads and can remain open long after their useful work. Side
conversations are managed separately by the Codex TUI.

### 7. Privacy and safety

The detector stores only the boolean side-active result. It does not persist
side questions, inherited history, labels, or thread IDs.

### 8. Documentation and release

Document state precedence:

1. currently displayed side conversation UI;
2. parent/main pane live UI when no side is selected;
3. exact durable main-thread Goal/runtime state;
4. hooks and completion fallback.

Release the change as v0.5.4.

## Testing

Add regression coverage for:

- active side label detection;
- parent-view `for side` hint is not active;
- side label outside the bounded tail is ignored;
- `PaneRow.side_active` defaults false;
- side-active idle pane ignores parent `goal_active`;
- side-active working pane ignores parent done hook;
- side-active blocked pane remains approval;
- parent prompt state is ignored while side is active;
- closing side changes row identity and restores parent Goal behavior;
- subagent edges are not consulted;
- full unit-test discovery.

## Acceptance criteria

1. A displayed side conversation uses its own visible working, approval, or
   idle state.
2. An active parent Goal does not force the side conversation to running.
3. Switching back to the parent immediately restores the parent Goal state.
4. Threads without an active side conversation preserve v0.5.3 behavior.
5. All tests pass with no new runtime dependency.
6. Local deployment leaves the tmux server, every pane identity, and every
   detected AI process identity unchanged.
7. v0.5.4 is pushed to `main`, tagged, and published as the latest GitHub
   Release.

## Implementation record

- Implemented the bounded side-context detector and pane identity field.
- Made displayed Codex side state bypass parent Goal/runtime, hook, and prompt
  state.
- Passed all 122 unit and privacy tests.
- Installed v0.5.4 with the tmux server, all 62 pane identities, and all 11
  detected AI process identities unchanged.
- Verified the installed side-precedence regression and unchanged parent Goal
  behavior when no side conversation is displayed.
- Restarted only `tmuxcue.service` and confirmed it remained active.
- Published v0.5.4 from `main` as an annotated tag and latest GitHub Release.
