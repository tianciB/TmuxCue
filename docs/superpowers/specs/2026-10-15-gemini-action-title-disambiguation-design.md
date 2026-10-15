# Gemini Action Title Disambiguation Design

**Date:** 2026-10-15
**Status:** Implemented and locally deployed
**Target release:** v0.5.1

## Problem

TmuxCue v0.5.0 treats a Gemini CLI native title beginning with
`✋ Action Required` as sufficient evidence that the user must approve a tool.

Gemini CLI 0.64.0 uses that same title for two different situations:

1. a real tool-confirmation dialog that is waiting for user input;
2. an interactive shell that has produced output, is no longer focused, and
   has crossed Gemini's inactivity threshold while the agent continues
   working automatically.

In the observed live incident, the Gemini pane had the action-required title
but no confirmation text in the bounded bottom-of-pane capture. The Gemini
process remained active, and TmuxCue repeatedly alternated the window between
the running moon and `❓`.

## Goals

- Keep an automatically executing Gemini pane in the shared running state even
  when Gemini uses its action-required title as an interactive-shell focus
  hint.
- Continue showing `❓` for a real Gemini confirmation dialog.
- Continue treating a bound `Notification` / `ToolPermission` hook event as
  high-confidence approval evidence.
- Let a newer live working observation replace an older approval event after
  the user approves a tool.
- Preserve existing Codex and Claude behavior.
- Deploy, push, tag, and release without sending input or signals to any
  existing process.

## Non-goals

- Changing Gemini CLI or its native title behavior.
- Sending focus, approval, cancellation, or keyboard input to the Gemini pane.
- Reading or persisting confirmation details, prompt content, or tool
  arguments.
- Changing Gemini lifecycle hook configuration.
- Changing session-binding or transcript-selection behavior.

## Design

### 1. Treat the Gemini action title as ambiguous

For Gemini only, `✋ Action Required` and the text `Action Required` are not
standalone blocked-state evidence.

TmuxCue first examines the same bounded final 12 screen lines already used for
approval detection:

- if the tail contains a current Gemini confirmation pattern, return
  `blocked`;
- otherwise, an action-required native title represents active work and
  returns `working`.

This rule is limited to Gemini. Codex and Claude retain their current title and
screen precedence.

### 2. Preserve high-confidence approval evidence

The existing Gemini confirmation-tail patterns remain approval evidence,
including current action-required dialog text, explicit permission text, and
allow/confirmation choices.

A bound Gemini `Notification` hook with `notification_type=ToolPermission`
continues to resolve to `approval`. Gemini emits that hook only after a tool
returns confirmation details and immediately before entering its
awaiting-approval state.

### 3. State precedence

Gemini raw-state precedence becomes:

1. bounded current confirmation dialog → `blocked`;
2. native `✦` or other explicit working title → `working`;
3. native `✋ Action Required` without a confirmation dialog → `working`;
4. bounded current working evidence → `working`;
5. `◇ Ready` or no live evidence → `idle`.

The daemon's existing timestamp precedence remains unchanged:

- a newer approval hook can override an older working scan;
- a newer working scan can override a stale approval hook;
- a newer completion event can still produce done when no newer live working
  evidence exists.

### 4. Confirmation patterns

The Gemini approval-tail matcher must recognize the real confirmation UI
without depending solely on the window title. Tests use representative current
dialog text such as:

- `Action Required`;
- `Allow once`;
- `Allow for this session`;
- explicit permission or confirmation prompts.

Historical text outside the final 12 lines remains ignored.

### 5. Documentation and release

Update the architecture and README to state that Gemini's action-required
native title is ambiguous and requires either a current confirmation dialog or
a bound permission hook before TmuxCue shows `❓`.

Release the fix as v0.5.1.

## Testing

Add regression coverage for:

- Gemini action-required title with no confirmation dialog → working;
- Gemini action-required title with a current confirmation dialog → blocked;
- Gemini action-required title with old confirmation text outside the bounded
  tail → working;
- Gemini ready title with a current confirmation dialog → blocked;
- explicit Gemini working and ready titles;
- newer working scans superseding older approval hooks;
- unchanged Codex and Claude approval behavior;
- full unit-test discovery.

## Acceptance criteria

1. The observed automatically executing Gemini pane displays a rotating moon,
   not `❓`, without receiving input or being restarted.
2. A real Gemini tool-confirmation dialog still displays `❓`.
3. Existing Codex and Claude tests remain green.
4. All tests pass with no new runtime dependency.
5. Local deployment leaves the tmux server, every pane identity, and every
   detected Codex, Claude, and Gemini process identity unchanged.
6. v0.5.1 is pushed to `main`, tagged, and published as the latest GitHub
   Release.

## Verification

Local verification on 2026-10-15 completed successfully:

- all 99 unit tests passed;
- dependency and tmux checks passed;
- the installed CLI reports v0.5.1;
- the TmuxCue user service is active;
- the tmux server identity remained unchanged;
- all 54 existing tmux pane identities remained unchanged;
- all 12 detected Codex, Claude, and Gemini process identities remained
  unchanged;
- the live Gemini pane retained its native action-required title but TmuxCue
  resolved it to `running` with the shared moon icon because no current
  confirmation dialog was present;
- the live Gemini pane remained present and received no input or signal.
