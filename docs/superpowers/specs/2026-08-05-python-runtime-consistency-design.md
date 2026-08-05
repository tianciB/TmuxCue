# TmuxCue Python Runtime Consistency Design

## Problem

TmuxCue 0.4.2 declares Python 3.10 or newer in `pyproject.toml`, but the
installer currently uses two different interpreter-selection rules:

- installation checks and tests run with `python3` from the caller's `PATH`;
- installed CLI, hook, and service wrappers always execute
  `/usr/bin/python3`.

On the affected host, `python3` resolves to Anaconda Python 3.12.4 while
`/usr/bin/python3` is Python 3.8.10. Installation therefore succeeds, but the
user service immediately fails while evaluating Python 3.9+ type-alias syntax
in `src/tmuxcue/metadata.py`. Because the service has `Restart=on-failure`, it
then enters a restart loop.

Live verification of the repaired service exposed a second pre-release issue.
A Codex `PermissionRequest` hook can be followed by automatic approval, after
which Codex continues working without emitting another lifecycle hook. The
daemon already intends a newer native pane scan to supersede such a cached
approval event, but `raw_state()` replaces the pane scan timestamp with the
older Codex `task_started` timestamp whenever runtime metadata reports
`working`. The approval event therefore appears newer forever and the window
incorrectly remains at `❓` while the pane visibly shows active work.

## Goals

- Use one Python 3.10+ interpreter for installation checks, the installed CLI,
  lifecycle hooks, the systemd user service, updates, and uninstall helpers.
- Fail installation before changing the active release when no compatible
  interpreter is available.
- Detect a broken installed launcher before declaring installation successful.
- Let a fresh pane scan showing active work supersede an older cached approval
  hook while preserving genuine approval dialogs.
- Preserve the existing guarantee that installation never terminates,
  restarts, attaches to, or sends input to tmux panes or AI processes.
- Repair the current installation by restarting only `tmuxcue.service`.
- Publish the correction as patch release `v0.4.3`.

## Non-goals

- Add support for Python 3.8 or 3.9.
- Change detector pattern matching, title formatting, the installed hook set,
  or terminal-title behavior beyond the timestamp-precedence correction.
- Add a timeout that could clear a genuine approval request merely because it
  has remained unanswered for a fixed duration.
- Restart the tmux server, tmux sessions, panes, Codex, or Claude.
- Introduce a virtual environment or third-party dependency.

## Considered Approaches

### 1. Pin the interpreter that passed installation checks

Resolve `python3` through the installer's current `PATH`, obtain its canonical
`sys.executable`, require Python 3.10 or newer, and store a release-local
`python` symlink to that executable. Installed wrappers execute
`$HOME/.local/share/tmuxcue/current/python`, so systemd does not depend on its
own `PATH`.

This is the selected approach. It is small, dependency-free, and makes the
checked interpreter identical to the runtime interpreter.

### 2. Make TmuxCue compatible with system Python 3.8

This would require changing the declared support policy and auditing the whole
codebase for Python 3.9/3.10 features. It increases maintenance scope and is
unnecessary because a compatible interpreter is already installed.

### 3. Inject the caller's Conda environment into systemd

This depends on shell initialization and environment-specific paths. It is
less deterministic than an absolute interpreter selected during installation.

## Detailed Design

### Interpreter selection

At the beginning of `scripts/install.sh`:

1. Resolve the `python3` selected by the current `PATH`.
2. Run a version check that requires `sys.version_info >= (3, 10)`.
3. Resolve and retain `sys.executable` as `PYTHON_BIN`.
4. Use `"$PYTHON_BIN"` for every Python invocation performed by installation,
   including dependency checks, tests, configuration, completion generation,
   title settings, rollback, and VS Code Remote configuration.

The version check runs before the installer changes the current release,
wrappers, service unit, or persistent user configuration.

### Installed runtime

Each release contains:

```text
~/.local/share/tmuxcue/releases/<timestamp>/python
```

This is a symlink to the validated absolute interpreter. The `current`
symlink continues to select the active release. The `tmuxcue`,
`tmuxcue-hook`, and `tmuxcue-service` wrappers execute:

```text
$HOME/.local/share/tmuxcue/current/python
```

The wrappers also export this location as `TMUXCUE_PYTHON`, allowing the
installed uninstall helper to use the same interpreter. A source-tree
uninstall falls back to resolving a compatible `python3` when that variable
and the release-local interpreter are unavailable.

### Installation health check

After switching `current` and installing the wrappers, but before updating
persistent configuration, the installer runs the installed
`tmuxcue version` command. This imports the same modules through the same
interpreter used by the service and catches launcher/import failures such as
the Python 3.8 crash.

The existing user-service restart remains limited to `tmuxcue.service`.
Installation then verifies that the service remains active after a short
stability interval rather than accepting only its transient startup state.
Any failure follows the existing rollback path.

### Approval-event precedence

`IndicatorDaemon.resolve_state()` compares lifecycle-event timestamps against
the timestamp of the latest independent state observation. Its existing rules
already produce the desired behavior:

- a hook newer than the latest pane scan can immediately show `approval`;
- a later pane scan that still detects `blocked` preserves `approval`;
- a later pane scan that detects `working` returns to `running`.

The defect is in `IndicatorDaemon.raw_state()`. When both Codex runtime
metadata and the current pane scan report `working`, the method records only
the older runtime `task_started` timestamp as the observation time. The fix
records the later of:

- the runtime event timestamp; and
- `last_scan`, when the current `PaneRow` also reports `working`.

This does not introduce a timeout. A real approval dialog remains protected by
the detector's `blocked` state or by a hook that is newer than the latest pane
scan. The change only allows concrete, newer evidence of continued work to
supersede a cached approval hook.

### Safety verification

Before repair, capture:

- the tmux server PID;
- every tmux pane and pane PID;
- every direct Codex/Claude process PID.

Run the corrected installer. Afterward, capture the same values and require
exact equality. Tmux display options may be refreshed, but no tmux server,
session, window, pane, or AI process is restarted or signaled.

## Tests

Update `tests/test_install_safety.py` to verify that:

- the installer requires Python 3.10 or newer;
- `/usr/bin/python3` is not embedded in generated runtime wrappers;
- the release-local interpreter is created and used by all wrappers;
- installation invokes the installed CLI as a smoke test;
- uninstall reuses the selected runtime interpreter;
- all existing forbidden tmux process-control command checks remain intact.

Update `tests/test_daemon.py` with a regression sequence matching the live
failure:

1. an older Codex runtime event reports `working`;
2. a later `PermissionRequest` hook reports `approval`;
3. a still later pane scan reports `working`;
4. the resolved visual state is `running`, not `approval`.

Keep the existing test proving that a hook newer than the most recent pane scan
still produces `approval`.

Run the complete unit-test suite with the selected Python 3.12 interpreter.
Also run the installed `tmuxcue version`, inspect the user service state and
recent journal, and confirm that window options begin updating while all
captured process identities remain unchanged.

## Versioning and Release

The current local `main`, remote `origin/main`, and release tag `v0.4.2` all
point to commit `7a211ec099147785c07b0020df0e9606ca9241d0`, so no upstream merge
is required before implementation.

After all tests and live safety checks pass:

1. Change the package version from `0.4.2` to `0.4.3` in every authoritative
   version location.
2. Commit the tested source, tests, documentation, and version bump.
3. Push `main` to `origin`.
4. Create and push annotated tag `v0.4.3`.
5. Create a GitHub Release for `v0.4.3` whose notes describe the mismatched
   install/runtime Python bug, the interpreter pinning fix, and the
   non-disruptive verification.

The GitHub CLI is not currently installed on the host. Release creation will
therefore use an authenticated GitHub API client if credentials are already
available; otherwise the branch and tag can still be pushed, and release
creation will require adding an authenticated GitHub client without changing
the tested source.

## Success Criteria

- `tmuxcue version` prints the installed version without an exception.
- `tmuxcue.service` is enabled and remains active without restart-loop errors.
- The service process uses the validated Python 3.10+ interpreter.
- A pane that continues working after automatic approval returns to the
  running indicator on the next scan.
- A genuine approval event newer than the latest pane scan still displays
  `❓`.
- Existing tmux server, pane, Codex, and Claude PIDs are unchanged.
- The full test suite passes.
- Remote `main`, tag `v0.4.3`, and GitHub Release `v0.4.3` contain the tested
  fix.
