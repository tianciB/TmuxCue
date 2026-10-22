# Codex Unbound Pane Isolation Design

**Date:** 2026-10-22
**Status:** Written specification approved; implementation in progress
**Delivery:** Fix, verify, deploy locally, and push to `origin/main`

## Problem and evidence

The `self-study` tmux pane briefly displayed the previous Codex thread's
topic and blocked indicator after a new Codex process started. Once the new
task was submitted and named, its displayed topic and running state matched
the current task.

Read-only investigation found an unsafe fallback in
`MetadataResolver.codex_metadata()`: when command-line identity, open files,
process logs, the native title, and creation-time proximity do not identify a
thread, the resolver selects a recently updated thread from the same working
directory. The chosen thread can predate the current process by many hours.
Its model, topic, session ID, Goal state, and hooks can then be attributed to
the wrong pane.

This source path and the recorded old blocked Goal are consistent with the
transient incident. A simultaneous snapshot of every binding input before
the new task was submitted is unavailable; the fix will therefore be
verified with deterministic synthetic fixtures as well as live observation.

The unmodified baseline passes all 123 unit and privacy tests.

## Goals

- Prevent a Codex tmux pane from inheriting an old thread solely because its
  working directory matches.
- Preserve existing explicit and process-backed bindings, native-title
  matching, and creation-time-proximity behavior.
- Leave direct-terminal, Claude, Gemini, Goal precedence, side-conversation,
  and visible approval detection behavior unchanged.
- Apply the fix without sending input or signals to any tmux or AI process.
- Push the tested fix to the existing `tianciB/TmuxCue` repository without
  rewriting remote history.

## Non-goals

- Resuming, modifying, completing, or deleting any Codex Goal.
- Changing Codex, its hooks, its databases, or another project's repository.
- Redesigning all heuristic Codex bindings or detecting new TUI layouts.
- Hard-coding a session name, pane ID, process ID, or thread ID.
- Changing global tmux configuration, terminal-title mode, or VS Code
  settings.
- Creating a tag or GitHub Release.

## Design

### 1. Make the final cwd-only fallback optional

Extend `codex_metadata()` with a keyword-only
`allow_cwd_fallback: bool = True` argument. `resolve()` passes `False` for a
Codex `PaneRow`, and keeps it enabled for a Codex `TerminalRow`.

The existing candidate filtering and selection order remain:

1. A command-line UUID matching an eligible user thread.
2. An eligible user thread identified by an open rollout or writer lock.
3. An eligible user thread identified by this process incarnation's logs.
4. A unique exact native-title match.
5. The existing closest creation time within 180 seconds of process start.

If none of these selects a candidate and cwd-only fallback is disabled,
return empty model, topic, and session ID instead of choosing an old or
otherwise unrelated same-directory thread. When fallback is enabled, keep
the existing final selection behavior.

The new option defaults to the existing behavior so direct resolver callers
retain compatibility. Only the Codex tmux integration opts into the narrower
behavior.

Creation-time proximity is still a heuristic, not proof of unique process
ownership. Changing that heuristic is outside this bounded fix.

### 2. Let existing unbound behavior handle display and state

For an unbound pane, `resolve()` already falls back to its sanitized native
subject or working-directory basename. It must not use the rejected
thread's model or topic.

An empty session ID must yield no runtime snapshot and no matching
thread-specific hook. The existing native pane detector determines whether
the pane is idle, working, or visibly requesting approval. Do not add a new
display state or change the state-priority rules.

Once the actual thread becomes discoverable, normal metadata refresh binds
it. Existing runtime-cache invalidation on session-ID changes remains in
use; no historical thread state is persisted by the new code.

### 3. Preserve failure handling and privacy

Missing, malformed, or unreadable databases continue to return unbound
metadata without interrupting the daemon. No new database or transcript
fields are read. Tests and committed documentation use synthetic topics,
thread IDs, and paths rather than private conversation content.

## Tests and acceptance criteria

1. With an idle Codex pane, no matching identity or nearby creation time,
   and one old same-directory thread, resolution returns no thread ID or
   old model/topic.
2. The same result holds with multiple old same-directory threads, even
   when another thread was updated most recently.
3. An old thread's blocked Goal, active Goal, or hook cannot change that
   unbound pane's state.
4. Explicit resume IDs, open rollout/writer-lock bindings, process-log
   bindings, and unique title matches still bind old threads correctly.
5. Existing nearby-creation selection remains unchanged.
6. A directly bound pane keeps its own metadata when another same-directory
   thread is updated.
7. Direct-terminal cwd fallback remains unchanged.
8. A subsequently discoverable current thread replaces unbound metadata
   through ordinary refresh.
9. All existing tests and the new regressions pass using Python 3.10+
   standard-library dependencies only.
10. README and architecture notes describe this narrow fallback restriction
    without claiming that every remaining heuristic is an exact binding.

## Local deployment and rollback

Use the existing installed-release layout to stage the tested source in a
new release directory while preserving the validated Python interpreter and
unchanged installed hooks and helper files. Atomically switch the `current`
symlink and restart only `tmuxcue.service`.

Do not run the full installer for this code-only fix: it also reapplies
global tmux and other application settings, which are outside this task.
Do not change any installed configuration or the user's selected title mode.

Immediately before and after deployment, compare the tmux server identity,
all pane identities, and all detected AI process identities. Confirm that
the service is active, check the installed fix with synthetic inputs, and
inspect `self-study` without sending it input. Its live state may naturally
change; validate the state actually observed rather than requiring it to
remain running.

If the service fails or verification fails, restore the previous `current`
symlink and restart only the display service. Investigate any identity
change without terminating or restarting the affected session.

## Git delivery

Commit only the reviewed documentation, implementation, regression tests,
and relevant README/architecture updates. Recheck the worktree and remote
`main` before pushing. Use a normal fast-forward push to `origin/main`;
never force-push or overwrite unrelated changes. Verify that remote `main`
and the local delivered commit agree before reporting completion.
