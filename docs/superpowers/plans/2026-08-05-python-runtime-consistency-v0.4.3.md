# Python Runtime Consistency v0.4.3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make installation and runtime use the same validated Python 3.10+ interpreter, repair the current installation without disturbing tmux panes or AI processes, and publish TmuxCue v0.4.3.

**Architecture:** `scripts/install.sh` resolves one compatible interpreter, records it in each immutable release as a `python` symlink, and makes every installed launcher execute that release-local path. Installation and uninstall helpers reuse the same interpreter, while a launcher smoke test and delayed systemd health check prevent a transiently active but crashing service from being accepted.

**Tech Stack:** Bash, Python 3.10+, Python `unittest`, tmux, systemd user services, Git, GitHub.

## Global Constraints

- Keep `requires-python = ">=3.10"` unchanged.
- Publish the fix as version `0.4.3` and tag `v0.4.3`.
- Add no runtime dependency and no virtual environment.
- Never run tmux `kill-*`, `new-session`, `send-keys`, `attach`, `respawn`, or `source-file`.
- Never restart or signal the tmux server, a tmux pane process, Codex, or Claude.
- The only process intentionally restarted during live repair is `tmuxcue.service`.
- Capture and compare the tmux server PID, all pane PIDs, and all Codex/Claude PIDs before and after installation.
- Do not push or tag until the complete test suite and live non-disruption checks pass.

---

## File Structure

- Modify `scripts/install.sh`: select and validate Python, create the release-local interpreter symlink, generate consistent wrappers, smoke-test the installed CLI, and check service stability.
- Modify `scripts/uninstall.sh`: reuse `TMUXCUE_PYTHON` or the active release interpreter, with a compatible source-tree fallback.
- Modify `tests/test_install_safety.py`: lock down interpreter consistency and preserve process-safety checks.
- Modify `src/tmuxcue/__init__.py`: set runtime version `0.4.3`.
- Modify `pyproject.toml`: set package version `0.4.3`.
- Modify `tests/test_cli.py`: expect the new public version.
- Modify `src/tmuxcue/service.py`: preserve a fresh working-pane observation
  when runtime metadata contains an older `task_started` timestamp.
- Modify `tests/test_daemon.py`: reproduce automatic approval followed by
  continued work.
- Use `docs/superpowers/specs/2026-08-05-python-runtime-consistency-design.md` as the release design record.

### Task 1: Add the Installer Runtime Regression Test

**Files:**
- Modify: `tests/test_install_safety.py`
- Test: `tests/test_install_safety.py`

**Interfaces:**
- Consumes: the text of `scripts/install.sh` and `scripts/uninstall.sh`.
- Produces: `InstallSafetyTests.test_installers_pin_the_validated_python_runtime`, which fails against v0.4.2 and passes only when launchers and helpers use one selected interpreter.

- [ ] **Step 1: Add the failing regression test**

Add this method to `InstallSafetyTests` after
`test_install_and_uninstall_never_control_panes`:

```python
    def test_installers_pin_the_validated_python_runtime(self):
        install = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        uninstall = (ROOT / "scripts/uninstall.sh").read_text(encoding="utf-8")

        self.assertIn("sys.version_info < (3, 10)", install)
        self.assertIn('PYTHON_BIN="$("$PYTHON_CANDIDATE"', install)
        self.assertIn('ln -s "$PYTHON_BIN" "$RELEASE/python"', install)
        self.assertIn(
            'export TMUXCUE_PYTHON="$HOME/.local/share/tmuxcue/current/python"',
            install,
        )
        self.assertEqual(install.count('exec "$TMUXCUE_PYTHON"'), 3)
        self.assertIn('"$BIN_DIR/tmuxcue" version >/dev/null', install)
        self.assertIn("sleep 1", install)
        self.assertNotIn("/usr/bin/python3", install)
        self.assertNotIn('PYTHONPATH="$ROOT/src" python3', install)

        self.assertIn(
            'PYTHON_BIN="${TMUXCUE_PYTHON:-$ROOT/python}"',
            uninstall,
        )
        self.assertIn("sys.version_info < (3, 10)", uninstall)
        self.assertNotIn('PYTHONPATH="$ROOT/src" python3', uninstall)
        self.assertNotIn('python3 "$ROOT/scripts/configure.py"', uninstall)
```

- [ ] **Step 2: Run the regression test and confirm the old installer fails**

Run:

```bash
PYTHONPATH=src "$HOME/anaconda3/bin/python3" \
  -m unittest \
  tests.test_install_safety.InstallSafetyTests.test_installers_pin_the_validated_python_runtime \
  -v
```

Expected: `FAIL`, first reporting that `sys.version_info < (3, 10)` is absent
from `scripts/install.sh`.

### Task 2: Pin the Validated Interpreter During Install

**Files:**
- Modify: `scripts/install.sh`
- Test: `tests/test_install_safety.py`

**Interfaces:**
- Consumes: `python3` from the caller's `PATH`, or `TMUXCUE_PYTHON` during a later self-update.
- Produces: `PYTHON_BIN`, an absolute compatible interpreter path; release-local `python`; and three wrappers using `TMUXCUE_PYTHON`.

- [ ] **Step 1: Select and validate Python before reading TmuxCue settings**

Immediately after `ROOT=...`, insert:

```bash
PYTHON_CANDIDATE="${TMUXCUE_PYTHON:-$(command -v python3 || true)}"
[ -n "$PYTHON_CANDIDATE" ] && [ -x "$PYTHON_CANDIDATE" ] || {
    echo "TmuxCue requires Python 3.10 or newer, but python3 was not found." >&2
    exit 1
}
"$PYTHON_CANDIDATE" - <<'PY'
import sys

if sys.version_info < (3, 10):
    raise SystemExit(
        f"TmuxCue requires Python 3.10 or newer; found {sys.version.split()[0]}."
    )
PY
PYTHON_BIN="$("$PYTHON_CANDIDATE" -c 'import os, sys; print(os.path.realpath(sys.executable))')"
[ -x "$PYTHON_BIN" ] || {
    echo "TmuxCue could not resolve a usable Python interpreter." >&2
    exit 1
}
```

Replace every installation or rollback invocation of bare `python3` with
`"$PYTHON_BIN"`. This includes `PREVIOUS_MODE`, rollback title restoration,
`__check`, the unit-test suite, `configure.py`, title-mode settings,
completion generation, and `tmuxcue.vscode install`.

- [ ] **Step 2: Add the selected interpreter to the immutable release**

After copying `src`, hooks, tmux configuration, and scripts into
`"$RELEASE"`, add:

```bash
ln -s "$PYTHON_BIN" "$RELEASE/python"
```

- [ ] **Step 3: Replace all three generated wrappers**

Use the following common runtime lines in `tmuxcue-service`, `tmuxcue-hook`,
and `tmuxcue`:

```bash
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="$HOME/.local/share/tmuxcue/current/src"
export PYTHONDONTWRITEBYTECODE=1
export TMUXCUE_PYTHON="$HOME/.local/share/tmuxcue/current/python"
```

End each wrapper with its corresponding command:

```bash
exec "$TMUXCUE_PYTHON" -m tmuxcue __service "$@"
```

```bash
exec "$TMUXCUE_PYTHON" -m tmuxcue.hook "$@"
```

```bash
exec "$TMUXCUE_PYTHON" -m tmuxcue "$@"
```

- [ ] **Step 4: Smoke-test the installed launcher**

Immediately after atomically switching `"$BASE/current"`, add:

```bash
"$BIN_DIR/tmuxcue" version >/dev/null
```

This must execute before the service unit and persistent configuration are
updated so the existing rollback restores the previous release and wrappers
if imports fail.

- [ ] **Step 5: Require the restarted service to remain active**

Replace the immediate post-restart check with:

```bash
systemctl --user restart tmuxcue.service
sleep 1
systemctl --user is-active --quiet tmuxcue.service
```

- [ ] **Step 6: Run the installer regression test**

Run:

```bash
PYTHONPATH=src "$HOME/anaconda3/bin/python3" \
  -m unittest \
  tests.test_install_safety.InstallSafetyTests.test_installers_pin_the_validated_python_runtime \
  -v
```

Expected: the install assertions pass; uninstall assertions still fail until
Task 3.

### Task 3: Reuse the Selected Interpreter During Uninstall

**Files:**
- Modify: `scripts/uninstall.sh`
- Test: `tests/test_install_safety.py`

**Interfaces:**
- Consumes: `TMUXCUE_PYTHON`, then `"$ROOT/python"`, then `python3` from `PATH`.
- Produces: one validated `PYTHON_BIN` for all Python-based uninstall cleanup.

- [ ] **Step 1: Resolve and validate the uninstall interpreter**

After `ROOT=...`, add:

```bash
PYTHON_BIN="${TMUXCUE_PYTHON:-$ROOT/python}"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3 || true)"
fi
[ -n "$PYTHON_BIN" ] && [ -x "$PYTHON_BIN" ] || {
    echo "TmuxCue requires Python 3.10 or newer to restore managed settings." >&2
    exit 1
}
"$PYTHON_BIN" - <<'PY'
import sys

if sys.version_info < (3, 10):
    raise SystemExit(
        f"TmuxCue requires Python 3.10 or newer; found {sys.version.split()[0]}."
    )
PY
```

- [ ] **Step 2: Replace uninstall's Python calls**

Use:

```bash
PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -m tmuxcue.vscode uninstall
PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -c 'from tmuxcue.settings import apply_title_mode; apply_title_mode("native")'
```

and:

```bash
"$PYTHON_BIN" "$ROOT/scripts/configure.py" uninstall
```

- [ ] **Step 3: Run the complete installer-safety test module**

Run:

```bash
PYTHONPATH=src "$HOME/anaconda3/bin/python3" \
  -m unittest tests.test_install_safety -v
```

Expected: all installer-safety tests pass.

- [ ] **Step 4: Check shell syntax**

Run:

```bash
bash -n scripts/install.sh scripts/uninstall.sh scripts/update.sh
```

Expected: exit status `0`, no output.

- [ ] **Step 5: Commit the runtime fix and regression test**

Run:

```bash
git add scripts/install.sh scripts/uninstall.sh tests/test_install_safety.py
git commit -m "fix: pin the validated Python runtime"
```

### Task 4: Bump the Patch Version to 0.4.3

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/tmuxcue/__init__.py`
- Modify: `pyproject.toml`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: the existing `__version__` API.
- Produces: public CLI and package version `0.4.3`.

- [ ] **Step 1: Change the CLI test first**

Replace:

```python
self.assertIn("TmuxCue 0.4.2", output.getvalue())
```

with:

```python
self.assertIn("TmuxCue 0.4.3", output.getvalue())
```

- [ ] **Step 2: Confirm the version test fails**

Run:

```bash
PYTHONPATH=src "$HOME/anaconda3/bin/python3" \
  -m unittest tests.test_cli.CliTests.test_version_and_help -v
```

Expected: `FAIL` because the CLI still prints `TmuxCue 0.4.2`.

- [ ] **Step 3: Update both authoritative version values**

Set `src/tmuxcue/__init__.py` to:

```python
"""TmuxCue."""

__version__ = "0.4.3"
```

Set the project version in `pyproject.toml` to:

```toml
version = "0.4.3"
```

- [ ] **Step 4: Run the CLI version test**

Run:

```bash
PYTHONPATH=src "$HOME/anaconda3/bin/python3" \
  -m unittest tests.test_cli.CliTests.test_version_and_help -v
```

Expected: `OK`.

- [ ] **Step 5: Commit the version bump**

Run:

```bash
git add pyproject.toml src/tmuxcue/__init__.py tests/test_cli.py
git commit -m "chore: release version 0.4.3"
```

### Task 5: Let a Fresh Working Scan Supersede Cached Approval

**Files:**
- Modify: `tests/test_daemon.py`
- Modify: `src/tmuxcue/service.py`
- Test: `tests/test_daemon.py`

**Interfaces:**
- Consumes: `PaneRow.state`, `IndicatorDaemon.last_scan`, and the Codex runtime
  state and event timestamp.
- Produces: an observation timestamp representing the freshest independent
  evidence when both the pane and runtime metadata report active work.

- [ ] **Step 1: Add the failing live-sequence regression test**

Add this method after
`test_fresh_hook_events_override_a_cached_working_scan`:

```python
    def test_fresh_working_scan_supersedes_older_approval_hook(self):
        daemon = IndicatorDaemon()
        now = time.time()
        row = PaneRow(
            "%1", "s:0", "codex", "working", "/tmp", "", "/dev/pts/1"
        )
        metadata = Metadata("codex", "model", "topic", "session")
        daemon.last_scan = now
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("working", now - 20)
        )

        raw_state = daemon.raw_state("s:0", row, metadata, now)

        self.assertEqual(raw_state, "working")
        self.assertEqual(daemon.observation_time(row), now)
        self.assertEqual(
            daemon.resolve_state(
                row,
                raw_state,
                None,
                {
                    "state": "approval",
                    "event": "PermissionRequest",
                    "timestamp": now - 5,
                },
                now,
            ),
            "running",
        )
```

- [ ] **Step 2: Run the regression test and confirm the old timestamp loses**

Run:

```bash
PYTHONPATH=src:tests "$HOME/anaconda3/bin/python3" \
  -m unittest \
  test_daemon.DaemonTests.test_fresh_working_scan_supersedes_older_approval_hook \
  -v
```

Expected: `FAIL` because `observation_time(row)` is `now - 20`, so the cached
approval hook incorrectly wins.

- [ ] **Step 3: Preserve the fresh pane observation**

In `IndicatorDaemon.raw_state()`, replace the direct assignment inside
`if runtime_state is not None:` with:

```python
            observation_time = event_time or self.runtime_times.get(target, now)
            if (
                runtime_state == "working"
                and isinstance(row, PaneRow)
                and row.state == "working"
            ):
                observation_time = max(observation_time, self.last_scan)
            self.raw_observation_times[target] = observation_time
```

Keep the existing `working`, `aborted`, and `idle` return branches unchanged.

- [ ] **Step 4: Run both approval-precedence tests**

Run:

```bash
PYTHONPATH=src:tests "$HOME/anaconda3/bin/python3" \
  -m unittest \
  test_daemon.DaemonTests.test_fresh_hook_events_override_a_cached_working_scan \
  test_daemon.DaemonTests.test_fresh_working_scan_supersedes_older_approval_hook \
  -v
```

Expected: both tests pass, proving:

- a hook newer than a cached scan immediately displays approval;
- a later scan that still observes work clears the cached approval state.

- [ ] **Step 5: Commit the state-precedence fix**

Run:

```bash
git add src/tmuxcue/service.py tests/test_daemon.py
git commit -m "fix: clear stale approval after work resumes"
```

### Task 6: Run the Complete Test Suite

**Files:**
- Verify: all source, script, and test files.

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces: a tested v0.4.3 candidate.

- [ ] **Step 1: Run all unit tests with the selected interpreter**

Run:

```bash
PYTHONPATH=src "$HOME/anaconda3/bin/python3" \
  -m unittest discover -s tests -q
```

Expected: all tests pass with exit status `0`.

- [ ] **Step 2: Run dependency and CLI smoke checks from the source tree**

Run:

```bash
PYTHONPATH=src "$HOME/anaconda3/bin/python3" -m tmuxcue __check
PYTHONPATH=src "$HOME/anaconda3/bin/python3" -m tmuxcue version
```

Expected:

```text
systemd-user: ok
tmux: ok
TmuxCue 0.4.3
```

`tmux` may report `optional (not installed)` only in an isolated test
environment; on the live host it must report `ok`.

- [ ] **Step 3: Confirm the worktree contains only intended commits**

Run:

```bash
git status --short --branch
git log --oneline --decorate origin/main..HEAD
```

Expected: a clean worktree containing only the design, implementation plan,
runtime fix, version bump, and any test-driven documentation cleanup ahead of
`origin/main`.

### Task 7: Repair the Current Installation Without Disturbing Other Processes

**Files:**
- Install from: repository root.
- Write outside repository through the existing installer:
  `~/.local/share/tmuxcue`, `~/.local/bin`, user systemd configuration, and
  managed hook/title settings.

**Interfaces:**
- Consumes: the tested installer and current live process identities.
- Produces: an active v0.4.3 service and an exact before/after process match.

- [ ] **Step 1: Capture the live safety baseline**

Run outside the filesystem/process sandbox:

```bash
mkdir -p /tmp/tmuxcue-v0.4.3-safety
pgrep -o -u "$(id -u)" tmux \
  > /tmp/tmuxcue-v0.4.3-safety/tmux.before
tmux list-panes -a \
  -F '#{session_name}:#{window_index}.#{pane_index}:#{pane_pid}' \
  | sort > /tmp/tmuxcue-v0.4.3-safety/panes.before
ps -eo uid=,pid=,comm= \
  | awk -v uid="$(id -u)" \
      '$1 == uid && ($3 == "codex" || $3 == "claude" || $3 == "claude-code") { print $2 ":" $3 }' \
  | sort > /tmp/tmuxcue-v0.4.3-safety/ai.before
date -u +%Y-%m-%dT%H:%M:%SZ \
  > /tmp/tmuxcue-v0.4.3-safety/install-started-at
```

Expected: all commands succeed; no tmux or AI process is controlled.

- [ ] **Step 2: Run the corrected installer**

Run outside the sandbox:

```bash
./install.sh
```

Expected:

```text
TmuxCue installed. Existing tmux and terminal AI processes were left untouched.
```

The command may refresh tmux display options and restarts only
`tmuxcue.service`.

- [ ] **Step 3: Capture and compare process identities**

Run:

```bash
pgrep -o -u "$(id -u)" tmux \
  > /tmp/tmuxcue-v0.4.3-safety/tmux.after
tmux list-panes -a \
  -F '#{session_name}:#{window_index}.#{pane_index}:#{pane_pid}' \
  | sort > /tmp/tmuxcue-v0.4.3-safety/panes.after
ps -eo uid=,pid=,comm= \
  | awk -v uid="$(id -u)" \
      '$1 == uid && ($3 == "codex" || $3 == "claude" || $3 == "claude-code") { print $2 ":" $3 }' \
  | sort > /tmp/tmuxcue-v0.4.3-safety/ai.after
cmp /tmp/tmuxcue-v0.4.3-safety/tmux.before \
    /tmp/tmuxcue-v0.4.3-safety/tmux.after
cmp /tmp/tmuxcue-v0.4.3-safety/panes.before \
    /tmp/tmuxcue-v0.4.3-safety/panes.after
cmp /tmp/tmuxcue-v0.4.3-safety/ai.before \
    /tmp/tmuxcue-v0.4.3-safety/ai.after
```

Expected: every `cmp` exits `0` with no output.

- [ ] **Step 4: Verify the installed runtime and service**

Run:

```bash
"$HOME/.local/bin/tmuxcue" version
readlink -f "$HOME/.local/share/tmuxcue/current/python"
systemctl --user is-enabled tmuxcue.service
systemctl --user is-active tmuxcue.service
MAIN_PID="$(systemctl --user show tmuxcue.service -p MainPID --value)"
readlink -f "/proc/$MAIN_PID/exe"
journalctl --user-unit=tmuxcue.service \
  --since "$(cat /tmp/tmuxcue-v0.4.3-safety/install-started-at)" \
  --no-pager -o short-iso
```

Expected:

- CLI output is `TmuxCue 0.4.3`.
- The release-local `python` and service executable both resolve to
  `$HOME/anaconda3/bin/python3.12`.
- The unit is `enabled` and `active`.
- The journal contains no traceback, failed result, or restart loop after the
  recorded installation time.

- [ ] **Step 5: Verify TmuxCue is updating tmux window options**

Run:

```bash
tmux list-windows -a \
  -F '#{session_name}:#{window_index}\t#{@agent_state}\t#{@agent_icon}\t#{@agent_title}'
```

Expected: windows containing detected Codex or Claude processes have non-empty
agent state, icon, and title fields. No session, window, or pane is created.

### Task 8: Synchronize, Push, Tag, and Publish v0.4.3

**Files:**
- Verify: Git history and remote refs.
- Temporary release notes: `/tmp/tmuxcue-v0.4.3-release.md`.

**Interfaces:**
- Consumes: the clean, tested, live-verified v0.4.3 commit.
- Produces: updated `origin/main`, annotated tag `v0.4.3`, and GitHub Release
  `v0.4.3`.

- [ ] **Step 1: Fetch and verify that remote main has not diverged**

Run:

```bash
git fetch origin
git merge-base --is-ancestor origin/main HEAD
git status --short --branch
```

Expected: the ancestry check exits `0` and the worktree is clean. If
`origin/main` advanced independently, rebase the local release commits onto
`origin/main`, rerun Tasks 6 and 7, and only then continue.

- [ ] **Step 2: Create the annotated release tag**

Run:

```bash
git tag -a v0.4.3 -m "TmuxCue v0.4.3"
git show --no-patch --decorate v0.4.3
```

Expected: `v0.4.3` points to the tested `HEAD`.

- [ ] **Step 3: Push the branch and tag**

Run:

```bash
git push origin main
git push origin v0.4.3
```

Expected: GitHub accepts both refs without force pushing.

- [ ] **Step 4: Write exact GitHub Release notes**

Create `/tmp/tmuxcue-v0.4.3-release.md` with:

```markdown
## Fixed

- Use the same validated Python 3.10+ interpreter for installation, the CLI,
  lifecycle hooks, the systemd user service, updates, and uninstall cleanup.
- Stop hardcoding `/usr/bin/python3`, which could pass installation with a
  newer PATH interpreter but crash the service under Python 3.8.
- Smoke-test the installed launcher and require the restarted service to remain
  active before installation is accepted.
- Clear a cached automatic-approval indicator when a newer native pane scan
  confirms that Codex is still working.

## Safety verification

- Confirmed the tmux server PID, every tmux pane PID, and all running
  Codex/Claude PIDs were unchanged during installation.
- Only `tmuxcue.service` was restarted.
```

- [ ] **Step 5: Authenticate a portable GitHub CLI if needed**

No GitHub API token or existing `gh` configuration is present, but SSH push
authentication for `tianciB` is working. Download and checksum-verify the
official portable GitHub CLI into `/tmp`:

```bash
GH_VERSION="$(
  curl -fsSL https://api.github.com/repos/cli/cli/releases/latest \
    | "$HOME/anaconda3/bin/python3" -c \
        'import json, sys; print(json.load(sys.stdin)["tag_name"].removeprefix("v"))'
)"
GH_ARCHIVE="gh_${GH_VERSION}_linux_amd64.tar.gz"
curl -fsSL \
  -o "/tmp/$GH_ARCHIVE" \
  "https://github.com/cli/cli/releases/download/v${GH_VERSION}/${GH_ARCHIVE}"
curl -fsSL \
  -o /tmp/gh-checksums.txt \
  "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_checksums.txt"
(
  cd /tmp
  grep " ${GH_ARCHIVE}$" gh-checksums.txt | sha256sum -c -
)
rm -rf /tmp/gh /tmp/gh-extract
mkdir -p /tmp/gh /tmp/gh-extract
tar -xzf "/tmp/$GH_ARCHIVE" -C /tmp/gh-extract
install -m 755 \
  "/tmp/gh-extract/gh_${GH_VERSION}_linux_amd64/bin/gh" \
  /tmp/gh/gh
```

Expected: checksum output is:

```text
gh_<resolved-version>_linux_amd64.tar.gz: OK
```

Then run:

```bash
/tmp/gh/gh auth login --hostname github.com --git-protocol ssh --web
```

Expected: GitHub presents a one-time device code. Complete that one-time web
authorization as `tianciB`; do not expose or store a token in the repository.

- [ ] **Step 6: Create and verify the GitHub Release**

Run:

```bash
/tmp/gh/gh release create v0.4.3 \
  --repo tianciB/TmuxCue \
  --verify-tag \
  --title "TmuxCue v0.4.3" \
  --notes-file /tmp/tmuxcue-v0.4.3-release.md
/tmp/gh/gh release view v0.4.3 \
  --repo tianciB/TmuxCue \
  --json tagName,name,isDraft,isPrerelease,url
```

Expected: `tagName` is `v0.4.3`, name is `TmuxCue v0.4.3`, and both
`isDraft` and `isPrerelease` are `false`.

- [ ] **Step 7: Perform final local and remote checks**

Run:

```bash
git status --short --branch
git ls-remote --heads --tags origin main v0.4.3
"$HOME/.local/bin/tmuxcue" version
systemctl --user is-active tmuxcue.service
```

Expected: clean `main` tracking `origin/main`, matching remote branch/tag refs,
installed version `TmuxCue 0.4.3`, and service state `active`.

## Self-Review

- The plan covers every design goal: interpreter selection, wrappers,
  uninstall, health checks, approval-event precedence, tests, live
  non-disruption proof, versioning, push, tag, and GitHub Release.
- All code-changing steps contain exact code or replacements.
- `PYTHON_CANDIDATE`, `PYTHON_BIN`, `TMUXCUE_PYTHON`, release-local `python`,
  and version `0.4.3` are named consistently across tasks.
- No task uses a prohibited tmux process-control command.
