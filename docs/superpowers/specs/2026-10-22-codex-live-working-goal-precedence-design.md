# Codex Live Working Goal Precedence Design

**Date:** 2026-10-22
**Status:** Implemented, deployed, and released
**Target release:** v0.5.5

## Problem

TmuxCue v0.5.4 lets an exact durable Codex Goal promote an idle pane:

- `goal_active` maps to running;
- `goal_blocked` maps to approval.

The durable Goal status can remain blocked after the user submits a new turn.
In that case the current Codex pane can show a live working spinner and a new
rollout `task_started` event while the older Goal row still reports blocked.
TmuxCue currently lets `goal_blocked` override that live pane evidence and
incorrectly renders approval.

The observed `self-study` case had:

- a current Codex working spinner;
- a current `UserPromptSubmit` hook;
- a current rollout `task_started` event;
- no visible approval dialog;
- an older durable `goal_blocked` update.

## Goals

- Treat a current Codex tmux working UI as newer state than a stale blocked
  Goal.
- Keep a current visible approval dialog authoritative.
- Continue using exact Goal state to supervise an idle main pane.
- Preserve v0.5.4 side-conversation precedence.
- Preserve direct-terminal, Claude, Gemini, hook, and stale-spinner behavior.
- Deploy and release without sending input or signals to any existing process.

## Non-goals

- Mutating or resuming the Codex Goal.
- Inferring whether a stalled Goal should be resumed.
- Parsing the textual `Goal stalled` footer.
- Comparing clocks from unrelated persistence layers.
- Changing Codex hook or Goal behavior.

## Design

### 1. Precedence

For a Codex tmux pane:

1. A displayed side conversation remains authoritative.
2. A current visible approval dialog remains blocked.
3. A current native working pane remains working even if the durable Goal
   reports `goal_blocked`.
4. When the pane is idle, exact Goal state may promote it to working or
   blocked.
5. Existing runtime and hook freshness rules continue to resolve stale UI.

The implementation changes only the `goal_blocked` branch. It does not
short-circuit all runtime processing for every working pane, because newer
`task_complete` and `turn_aborted` evidence must still be able to supersede a
stale spinner.

### 2. Observation time

When a live working pane overrides `goal_blocked`, record the current pane
scan time as the raw observation time. This keeps older Goal and hook evidence
from replacing the current UI while still allowing a newer approval hook to
win.

### 3. Idle Goal supervision

An idle pane with an exact blocked Goal still maps to approval. An idle pane
with an exact active Goal still maps to running. Direct-terminal Goal behavior
is unchanged because there is no native tmux pane observation to prioritize.

### 4. Side conversations

The v0.5.4 side-active short circuit remains first. Parent Goal, hook, and
prompt state stay ignored while a side conversation is displayed.

### 5. Privacy and safety

No new content is read or stored. The change uses the existing pane state and
exact Goal status. Installation may restart only `tmuxcue.service` and must
preserve the tmux server, every pane identity, and every detected AI process
identity.

## Testing

Add regression coverage for:

- working Codex pane plus `goal_blocked` maps to running;
- the current pane scan is the winning observation;
- idle Codex pane plus `goal_blocked` remains approval;
- visible blocked pane remains approval;
- side-active behavior remains unchanged;
- newer runtime completion and abort behavior remains unchanged;
- full unit and privacy test discovery.

## Documentation and release

Document that exact Goal state supplements an idle pane but does not override
current working or approval UI. Release the fix as v0.5.5.

## Acceptance criteria

1. The live `self-study` working pane displays running rather than approval.
2. An idle main pane with a blocked Goal still displays approval.
3. A visible approval dialog still displays approval.
4. Side-conversation precedence remains unchanged.
5. Existing stale-spinner completion and abort tests continue to pass.
6. All tests pass with no new runtime dependency.
7. Local deployment leaves the tmux server, every pane identity, and every
   detected AI process identity unchanged.
8. v0.5.5 is pushed to `main`, tagged, and published as the latest GitHub
   Release.

## Implementation record

- Added a regression for a working Codex pane with an older blocked Goal.
- Limited the precedence exception to `goal_blocked` so ordinary runtime
  completion and abort handling remains unchanged.
- Passed all 123 unit and privacy tests.
- Installed v0.5.5 with the tmux server, all 58 pane identities, and all 11
  detected AI process identities unchanged.
- Verified the installed synthetic regression.
- Verified live `self-study` state as working plus `goal_blocked`, correctly
  rendered as running.
- Restarted only `tmuxcue.service` and confirmed it remained active.
- Published v0.5.5 from `main` as an annotated tag and latest GitHub Release.
