# Gemini CLI Supervision Design

**Date:** 2026-10-15
**Status:** Implemented and locally deployed
**Target release:** v0.5.0

## Problem

TmuxCue supervises Codex and Claude sessions but does not recognize Gemini CLI.
An interactive Gemini CLI therefore keeps its native tmux title and does not
receive TmuxCue's consistent approval, running, and completed indicators,
model/topic label, direct-terminal support, or session-isolation guarantees.

Gemini CLI is Node-based rather than exposing a stable `gemini` process name.
It may also store settings and transcripts beneath either the default user
home or a per-process `GEMINI_CLI_HOME`. Multiple sessions can share the same
working directory, so choosing the most recently modified transcript alone
would leak state or topics between sessions.

## Goals

- Give Gemini CLI the same TmuxCue presentation as Codex and Claude:
  - `❓` while approval or user action is required;
  - the rotating moon phase while running in tmux;
  - stable `🌒` while running in an opted-in regular terminal;
  - `✅` after a turn completes or is interrupted.
- Format Gemini titles identically to the existing agents:
  `ICON [tmux|term] gemini/MODEL: TOPIC`.
- Detect official Node-based Gemini CLI processes without treating unrelated
  Node programs as Gemini sessions.
- Bind every supervised row to its actual Gemini session when multiple
  sessions use the same working directory.
- Read the bound session's model and latest meaningful user topic without
  copying prompt or response content into TmuxCue event files.
- Support the default Gemini home and safe custom `GEMINI_CLI_HOME` values.
- Preserve native Gemini titles by default and integrate Gemini into
  TmuxCue's opt-in regular-terminal title override.
- Install, update, deploy, and uninstall without restarting, signalling,
  attaching to, or sending input to any existing Codex, Claude, Gemini,
  tmux, training, or unrelated process.
- Preserve all existing Codex and Claude behavior.

## Non-goals

- Approving, denying, or modifying Gemini tool permission requests.
- Replacing or wrapping the user's Gemini launcher.
- Modifying Gemini project-level `.gemini/settings.json` files.
- Persisting prompt text, model output, tool arguments, permission details, or
  notification messages in TmuxCue's event directory.
- Managing Gemini sessions, deleting history, or changing the selected model.
- Adding a dependency outside the Python standard library.

## Design

### 1. Strict Gemini process discovery

Extend the existing bounded `/proc` scan to recognize Gemini CLI from exact
command-line structure rather than the generic Node process name.

Accepted evidence must identify an official Gemini CLI entry point, including
the installed `@google/gemini-cli` package or its `node_modules/.bin/gemini`
launcher. A bare occurrence of the word `gemini` in an arbitrary Node command
is insufficient.

The process must still:

- belong to the current user;
- be alive and in the foreground process group for its TTY;
- have a numeric terminal device under `/dev`;
- pass the existing stopped/zombie exclusions.

When one Gemini CLI invocation contains multiple Node layers, select one
stable foreground Gemini process for the `(TTY, gemini)` key. The selected
process identity consists of PID, process start time, command line, and TTY.
Codex and Claude discovery keeps its existing behavior.

Both `TmuxDetector` and `TerminalDetector` consume the shared process snapshot.
`TmuxDetector.identify_agent()` also accepts a native
`pane_current_command=gemini` when available.

### 2. Native tmux state detection

Gemini's native dynamic title is a live fallback and an observation that can
supersede an older hook event:

- a title beginning with `✋` or containing `Action Required` is `blocked`;
- a title beginning with `✦` or containing `Working` is `working`;
- a title beginning with `◇` or containing `Ready` is `idle`.

Gemini-specific approval and working patterns may also be matched only within
the same bounded bottom-of-pane tail used for Codex and Claude. Historical
text outside that tail must not keep a pane blocked or running.

TmuxCue's final visual mapping remains shared by all agents. Gemini does not
receive its own icons or title layout.

### 3. Gemini lifecycle hooks

Add `hooks/gemini-hooks.json.in` with these user-level hook groups:

| Gemini event | Matcher | TmuxCue state |
|---|---|---|
| `SessionStart` | all | `done` |
| `BeforeAgent` | all | `running` |
| `Notification` | `ToolPermission` | `approval` |
| `AfterAgent` | all | `done` |
| `SessionEnd` | all | `ended` |

Every command invokes:

```text
"$HOME/.local/bin/tmuxcue-hook" --agent gemini --state STATE
```

The hook reader accepts Gemini's snake-case base input, returns exactly `{}` on
stdout for Gemini, and never sets permission decisions or flow-control fields.
It records only:

- agent;
- session ID;
- state;
- hook event name;
- event timestamp;
- exact Gemini ancestor PID;
- that process's start time;
- that process's TTY.

The hook process walks its own parent chain through any shell used to run the
configured command. It accepts an ancestor only when that process passes the
same strict Gemini command identification used by normal discovery. If no
safe ancestor is found, the lifecycle event may still be stored by session ID,
but it must not create a PID binding.

No prompt, transcript path, working directory, notification message, tool
name, file path, or permission detail is copied into the event file.

### 4. Exact event-to-process session binding

Gemini metadata resolution first searches recent Gemini events for a binding
whose:

- PID equals the detected Gemini PID;
- stored process start time equals the detected start time;
- TTY equals the detected TTY;
- event timestamp is not older than the process incarnation.

An exact match supplies the session ID. PID equality without matching start
time is rejected to prevent PID-reuse leaks. TTY equality without PID equality
is not enough to bind a session.

If a currently running pre-hook Gemini session has no exact event binding,
metadata resolution may use transcript candidates whose project root matches
the process working directory and whose session start is compatible with the
process start. The fallback is accepted only when exactly one candidate
remains. Ambiguous candidates produce an unbound Gemini label rather than
borrowing another session's topic, model, or hook state.

### 5. Gemini home and transcript resolution

For a detected Gemini process, read only the `GEMINI_CLI_HOME` variable from
that process's `/proc/<pid>/environ`. Do not log, return, or retain any other
environment variable.

The Gemini state directory is:

- `$GEMINI_CLI_HOME/.gemini` when the variable is a safe absolute path owned
  by the current user; otherwise
- `~/.gemini`.

Session candidates are read from the Gemini history layout beneath that state
directory. A transcript is accepted only when its header contains a valid
session UUID and the session/project association matches the requested row.
Malformed, missing, oversized, or unreadable records fail closed.

For an exactly bound session:

- model is the newest non-empty `model` from a Gemini response;
- if absent, model falls back to a `--model` command-line argument and then
  the configured Gemini model name;
- topic is the latest non-empty, non-generic user text in that session;
- all topic text passes the existing cleaning and display-width shortening.

The resolver must not return content from a different session merely because
that file is newer.

### 6. Runtime state precedence

Gemini hook events use the same final-state resolver as Codex and Claude:

- a newer `BeforeAgent` event forces running while the detector catches up;
- a newer `ToolPermission` notification forces approval;
- `AfterAgent` produces done unless newer live tmux evidence still shows
  working;
- `SessionEnd` removes the indicator once the process/session has ended;
- a newer live Gemini title or bounded screen observation can replace a stale
  approval or completion event.

Direct terminals use hook state when available. They keep the existing stable
`🌒` running icon to avoid an unsolicited animation stream on the TTY.

### 7. Safe settings merge and removal

Installation manages only user-level Gemini settings:

1. the default `~/.gemini/settings.json`;
2. safe custom Gemini settings paths discovered from currently running,
   current-user Gemini processes.

Before modifying an existing file, create a mode-`0600` timestamped backup.
Merge only TmuxCue hook groups, identified by the existing TmuxCue hook command
marker. Preserve all unrelated settings and hook groups.

Record every managed Gemini settings path in TmuxCue's private configuration
directory so uninstall can remove TmuxCue groups even if no Gemini process is
running then. The recorded path must remain absolute, user-owned, and point to
the expected `.gemini/settings.json` location before it is reused.

Do not modify repository-local `.gemini/settings.json` files.

Malformed JSON causes configuration to stop with an error before replacing the
file. It must not overwrite the malformed settings with an empty object.

### 8. Terminal-title modes

Native mode leaves Gemini's `ui.dynamicWindowTitle` and
`ui.showStatusInTitle` values unchanged.

When the user opts into `tmuxcue config terminal-title override`, preserve the
original presence and value of both settings for every managed Gemini user
settings file, then set both to `false`. This prevents Gemini's native OSC
title updates from racing TmuxCue in regular terminals.

Returning to native mode or uninstalling restores the exact original state:

- restore the original value when the key existed;
- remove only the TmuxCue-added key when it did not exist;
- leave later unrelated settings untouched.

Like the existing Codex and Claude behavior, title-setting changes apply
without restarting or signalling an existing agent. If a running Gemini
version does not reload them dynamically, the change takes effect in a newly
started Gemini process.

### 9. Install, update, and uninstall safety

Replace the shell-only AI process snapshot with a shared snapshot capable of
including Node-based Gemini CLI identities. Safety checks compare, before and
after the operation:

- tmux server PID;
- every tmux pane PID;
- every detected Codex, Claude, and Gemini PID, process start time, TTY, and
  agent identity.

The scripts continue to prohibit process-control commands and do not call
`send-keys`, `attach`, `respawn`, or any tmux kill command. Deployment may
restart only the TmuxCue user service. A changed AI or pane identity fails the
safety check and reports the failure.

The service and scripts never restart Gemini to activate hooks. Existing
pre-hook Gemini sessions remain supervised through native-title and
conservative transcript fallback; newly loaded hooks improve event precision.

### 10. Documentation and release

Update:

- `README.md`;
- `docs/ARCHITECTURE.md`;
- hook review instructions;
- privacy and security guarantees;
- install/uninstall wording;
- package and CLI descriptions;
- tests that assert the public version.

The documentation must describe Codex, Claude, and Gemini consistently, show a
Gemini example title, list the five managed Gemini hooks, and state that the
permission notification is observational only.

Release the feature as v0.5.0.

## Testing

Add or extend tests for:

- strict Gemini Node command recognition and false-positive rejection;
- selection of a single Gemini layer on one foreground TTY;
- tmux detection from `✦`, `✋`, and `◇` native titles;
- Gemini direct-terminal discovery;
- Gemini hook JSON output and parent-process binding;
- event privacy: prompt, transcript, notification, and tool details are not
  persisted;
- exact PID/start-time/TTY session binding;
- PID reuse rejection;
- default and custom `GEMINI_CLI_HOME` resolution;
- unique legacy-session fallback and ambiguous-fallback refusal;
- JSONL model/topic extraction for the bound session;
- Gemini lifecycle state precedence;
- safe settings merge, backup, path recording, and uninstall removal;
- native/override title-setting preservation and restoration;
- malformed settings refusal;
- install/uninstall process-snapshot coverage for Gemini;
- unchanged Codex and Claude behavior;
- full unit-test discovery.

## Acceptance criteria

1. A Gemini tmux pane displays the same `❓`, rotating moon, and `✅` states
   and the same `[tmux] agent/model: topic` layout as Codex and Claude.
2. An opted-in regular terminal displays the same stable running icon and
   `[term]` layout used by the other agents.
3. Two Gemini sessions in the same directory never receive each other's
   session ID, hook state, model, or topic.
4. The observed custom-home Gemini invocation using
   `GEMINI_CLI_HOME` is recognized without changing its launcher.
5. Existing Gemini, Codex, Claude, tmux pane, and unrelated process identities
   are unchanged across local deployment.
6. Uninstall removes only TmuxCue's Gemini hooks and restores managed title
   settings while leaving all sessions running.
7. All tests pass under Python 3.10 or newer with no added runtime dependency.

## Verification

Local verification on 2026-10-15 completed successfully:

- dependency and tmux checks passed;
- all 96 unit tests passed;
- the installed CLI reports v0.5.0;
- the TmuxCue user service is active;
- the tmux server identity remained unchanged;
- all 54 existing tmux pane identities remained unchanged;
- all 11 detected Codex, Claude, and Gemini process identities remained
  unchanged;
- one live Gemini tmux row was recognized with a bound model, session, and
  topic;
- the live Gemini row rendered the shared done icon and
  `[tmux] gemini/MODEL: TOPIC` title layout;
- both managed Gemini user settings files contain all five TmuxCue hooks.
