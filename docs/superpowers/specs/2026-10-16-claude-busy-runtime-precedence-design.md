# Claude Busy Runtime Precedence Design

**Date:** 2026-10-16
**Status:** Implemented and locally deployed
**Target release:** v0.5.2

## Problem

TmuxCue can show a completed `✅` state while Claude Code is still working when
Claude uses a custom model backend.

The observed pane runs Claude Code CLI with a Gemini-backed model. Claude's
native session record reports `status=busy`, the terminal continues changing,
and the process remains active. However, the custom backend can cause a
`Stop` hook and a brief `idle` transition between phases before the same
session returns to `busy`.

TmuxCue currently returns an idle pane observation before consulting Claude's
runtime session state. As a result, the later `busy` state is ignored and the
older completion event remains visible.

The native Claude title begins with a static decorative `✳` followed by the
derived session name. That title remains stable across activity changes and
is not safe standalone working evidence.

## Goals

- Treat a live Claude `status=busy` session as working even when the native
  pane title does not expose a recognized spinner.
- Let the current observation that the session remains busy supersede an older
  `Stop` event.
- Preserve approval dialogs as the highest-priority pane state.
- Return to done when Claude reports a non-busy status.
- Keep direct-terminal behavior and all Codex and native Gemini behavior
  unchanged.
- Deploy, push, tag, and release without sending input or signals to any
  existing process.

## Non-goals

- Treating the static `✳` Claude title prefix as working evidence.
- Changing Claude Code, the custom API proxy, or model configuration.
- Suppressing or modifying Claude lifecycle hooks.
- Reading prompt or response content for activity detection.
- Restarting the affected Claude session.

## Design

### 1. Consult runtime state for Claude tmux panes

`IndicatorDaemon.raw_state()` currently returns every non-working tmux pane
observation before polling runtime metadata.

Change that early-return rule:

- a pane whose detector state is `blocked` still returns `blocked`
  immediately;
- non-Claude panes whose detector state is not `working` retain the existing
  immediate-return behavior;
- a Claude pane whose detector state is idle continues into runtime-state
  resolution.

This keeps the change scoped to the agent with a reliable local live session
status.

### 2. Promote a current Claude busy status to working

When `MetadataResolver.runtime_snapshot()` returns `working` for a Claude pane:

- return raw `working`;
- record the observation time as at least the current tmux scan time.

The current scan-time observation is deliberate. The session file is read
now and still says `busy`, even if its stored transition timestamp predates a
premature `Stop` event. Treating the read as current live evidence allows the
daemon's existing timestamp resolver to prefer ongoing work over that older
completion event.

### 3. Preserve approval and completion behavior

- A detector result of `blocked` remains approval even when the session file
  says busy.
- A bound, newer approval hook can still win when it is newer than the most
  recent live observation.
- Claude runtime `idle` or `aborted` continues to resolve to done.
- When no Claude runtime snapshot is available, fall back to the detector
  state exactly as before.
- Direct terminals keep their current runtime and hook precedence; the
  scan-time promotion is limited to `PaneRow`.

### 4. Static Claude titles

Do not add `✳` to generic working-title detection. The symbol is part of
Claude's generated session title and is not an activity animation.

Model and topic extraction remain unchanged. A Claude CLI session using a
Gemini-backed model continues to display the model returned by Claude's local
transcript and session metadata.

### 5. Documentation and release

Document that Claude runtime `busy` is authoritative working evidence for tmux
panes when custom backends emit intermediate or premature stop events.

Release the fix as v0.5.2.

## Testing

Add regression coverage for:

- Claude pane detector idle + runtime busy → raw working;
- runtime busy observed now supersedes an older `Stop` hook;
- Claude blocked pane + runtime busy → approval;
- Claude detector idle + runtime idle → done;
- absent Claude runtime state → detector fallback;
- Codex idle panes retain the existing immediate-return behavior;
- static `✳` titles are not independently classified as working;
- full unit-test discovery.

## Acceptance criteria

1. The affected Claude Code pane using a Gemini-backed model shows a rotating
   running moon while its local session status is busy.
2. A real approval dialog still shows `❓`.
3. When the session returns to idle, TmuxCue shows `✅`.
4. Existing Codex, Claude direct-terminal, and native Gemini tests remain
   green.
5. All tests pass with no new runtime dependency.
6. Local deployment leaves the tmux server, every pane identity, and every
   detected AI process identity unchanged.
7. v0.5.2 is pushed to `main`, tagged, and published as the latest GitHub
   Release.

## Verification

Local verification on 2026-10-16 completed successfully:

- all 104 unit tests passed;
- dependency and tmux checks passed;
- the installed CLI reports v0.5.2;
- the TmuxCue user service is active;
- the tmux server identity remained unchanged;
- all 55 existing tmux pane identities remained unchanged;
- all 10 detected Codex, Claude, and Gemini process identities remained
  unchanged;
- the live incident was observed with a bound Claude session reporting busy
  while TmuxCue still showed done before the fix;
- regression coverage confirms a current Claude busy read produces running and
  supersedes an older Stop event;
- the affected live session had naturally returned to idle by deployment time
  and correctly remained done;
- no pane or AI process received input or a signal.
