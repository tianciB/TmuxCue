# Claude Live Footer Activity Design

**Date:** 2026-10-28
**Status:** Implemented and locally deployed; remote delivery pending
**Delivery:** Safely deploy the fix and push to `origin/main`

## Problem and evidence

The affected `gemini-1` window runs Claude CLI with a Gemini-backed model,
not the native Gemini CLI. TmuxCue can show completed while the displayed
task is still working or waiting for a background agent.

The detector recognizes interrupt hints such as `Esc to interrupt`, but
does not recognize these observed Claude status forms:

```text
✽ Working… (1m 58s · ↓ 1.5k tokens)
✻ Waiting for 1 background agent to finish
```

Replaying both forms through the unmodified detector returns `idle`.
Claude's bound `busy` session record ordinarily compensates for that missing
UI evidence. When the main turn becomes idle while background work is still
pending, the missing evidence allows an intermediate completion to show.

The task later genuinely completed and displayed a completion summary.
That final completed state must remain completed; the fix must not force a
window to stay running just because it has a decorative title or old output.

The approved approach is scoped live-footer recognition, not a completion
delay, a model/backend change, or a manual override.

## Goals and non-goals

- Recognize the current Claude progress or positive background-agent wait
  status immediately above the input composer.
- Keep visible approvals authoritative.
- Ignore historical, quoted, completed, interrupted, and malformed status
  text; do not treat a static `✳` title as activity.
- Preserve Codex, native Gemini, direct-terminal, metadata binding, and
  lifecycle hook behavior.
- Preserve Python `>=3.10` and the empty runtime dependency list.
- Do not inspect background-agent transcripts, task registries, tool
  arguments, or model responses to infer activity.
- Do not send input or signals to tmux or AI processes.
- Do not change global tmux options, hooks, application settings, or models.
- Do not create a tag or GitHub Release.

## Design

### 1. Locate a current, bounded Claude footer

Inspect only the final 20 captured pane lines. Find the last Claude input
prompt beginning with `❯`. Require the upper composer separator immediately
before that prompt and a lower separator after it; separators consist of
horizontal box-drawing characters.

Skip blank lines above the upper separator. Only the immediately preceding
nonblank line can be the current status. Do not search all earlier output
for an activity phrase. If the composer or status is incomplete or outside
the bounded tail, keep the existing detection fallback.

### 2. Accept two constrained activity forms

The status must start with a Claude activity marker, not ordinary prose,
a quoted line, a code fence, or a terminal title.

- A progress label ending in `…` or `...`, followed by parentheses containing
  an elapsed duration and a directional token counter. Optional additional
  meter detail after the counter is permitted.
- The exact `Waiting for N background agent(s) to finish` status with a
  positive integer count.

Completed summaries such as `Baked for ... · done ...`, interrupted notices,
zero-agent waits, and generic background-terminal notices are not activity.

### 3. Reuse existing state precedence

Add the Claude-only footer check after existing approval detection and
before the generic interrupt-hint fallback. It returns the existing raw
`working` state; no new state or persisted field is introduced.

The daemon already allows a fresh working pane scan to supersede older
runtime-idle and Stop evidence. Keep that behavior unchanged. A newer
completion or approval may still supersede a stale captured working state.
The existing bound Claude `busy` observation remains useful when the footer
is absent or an unrecognized layout is displayed.

### 4. Privacy and scope

Use the already captured screen; add no command, database query, hook, or
process scan to normal detection. Store no footer text. Test fixtures use
generic labels and artificial process/session identities.

## Acceptance criteria

1. Current progress footers without interrupt hints produce `working`.
2. Current positive background-agent waits produce `working`.
3. Current visible approvals still produce `blocked`.
4. Fresh recognized activity survives older idle/Stop evidence and displays
   the running indicator.
5. Genuine completion returns to done without a new grace period.
6. Historical/quoted activity, earlier composers, zero-agent waits, static
   titles, and malformed or out-of-tail frames do not create activity.
7. Newer completion and approval evidence still supersede a stale scan.
8. Codex and native Gemini do not gain Claude-specific detection.
9. All existing tests and the added detector/daemon regressions pass.
10. Only the display service restarts during deployment; tmux server, pane,
    and AI process identities and checked configurations remain unchanged.
11. The tested implementation is pushed normally to the existing repository,
    and local and remote delivered commit IDs are verified equal.

## Deployment and rollback

Stage a copy of the current installed release with only the changed
`src/tmuxcue/detector.py`. Keep its validated interpreter, hooks, helper
files, and existing Codex isolation fix. Run the full suite against the
staged runtime before activating it.

Capture the tmux server identity, pane identities and live process start
times, AI process identities, global tmux options, and application/wrapper
configuration fingerprints. Preserve any pre-existing retained dead panes.
Atomically switch the installed-release symlink and restart only
`tmuxcue.service`. Re-run tests against the installed package and compare
the before/after snapshots.

Observe the target without sending input. If it has genuinely finished,
verify completed behavior rather than fabricating a running condition.
Use synthetic fixtures to verify the waiting/progress condition when it is
not present live.

On failed deployment verification, restore the previous release symlink and
restart only the display service. Retain the old release for rollback and
keep private audit snapshots outside the repository.

## Implementation and verification record

- Implemented the Claude-only detector update in `16857b4`. No daemon,
  metadata, hook, direct-terminal, or other-agent production logic changed.
- Added 22 detector/integration tests. Before the fix, the positive progress
  and waiting regressions reproduced idle/done misclassification; afterward
  all new tests passed.
- All 157 unit and privacy tests passed against repository source, the
  staged installed runtime, and the activated installed runtime.
- The source and regression module parse with Python 3.10 grammar; runtime
  verification used the existing validated Python 3.12 interpreter.
- Only `src/tmuxcue/detector.py` differed between installed releases,
  excluding existing bytecode caches. Its activated digest matches the
  committed source.
- The tmux server, all 59 panes, live pane process start times, and all 12
  detected AI process identities remained unchanged. One previously dead,
  retained pane remained dead and untouched.
- Global tmux options and 14 checked configuration/wrapper paths remained
  unchanged. Only the display service restarted; it is active with no
  automatic restarts or reported errors during verification.
- At the final live check, `gemini-1` had a bound working runtime and
  correctly displayed running with matching metadata. Its captured footer
  was not an activity footer at that instant; the newly recognized progress
  and background-wait paths were verified with synthetic fixtures, not by
  sending input to the live session.
- The previous installed release and private audit snapshots were retained
  for rollback. No private conversation content or snapshots were committed.
