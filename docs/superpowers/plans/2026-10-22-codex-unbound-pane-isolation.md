# Codex Unbound Pane Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a new Codex tmux pane from borrowing an unrelated old thread's metadata and state.

**Architecture:** Add an optional final cwd-only fallback to the existing Codex metadata resolver. Disable only that fallback for tmux panes, retaining all earlier selection paths and direct-terminal behavior. Existing unbound metadata and daemon state handling need no production changes.

**Tech Stack:** Python 3.10+ standard library, unittest, SQLite fixtures, tmux CLI, systemd user service, Bash, Git.

**Execution:** Inline in the existing checkout, as requested. The optional executing-plans skill is not installed; use the explicit test, implementation, review, and deployment checkpoints below. No subagents or separate tasks are needed.

## Global Constraints

- Preserve existing explicit and process-backed bindings, native-title matching, and creation-time-proximity behavior.
- Leave direct-terminal, Claude, Gemini, Goal precedence, side-conversation, and visible approval detection behavior unchanged.
- Apply the fix without sending input or signals to any tmux or AI process.
- No changes to global tmux configuration, terminal-title mode, or VS Code settings.
- Python support remains `>=3.10`; runtime dependencies remain empty.
- Do not run the full installer for this code-only fix.
- Push to `origin/main` without force-pushing; do not create a tag or GitHub Release.
- Commit only synthetic fixtures and generic evidence, never private conversation content or live process snapshots.

## File Responsibilities

- `src/tmuxcue/metadata.py`: opt-in restriction on the final Codex fallback.
- `tests/test_metadata.py`: isolated SQLite/process fixtures and resolver-to-daemon regressions using the existing database helpers.
- `README.md`: explain unbound Codex panes and the preserved compatibility paths.
- `docs/ARCHITECTURE.md`: document the distinction between cwd fallback and stronger binding evidence.
- This plan and its approved specification: execution and verification record.

---

### Task 1: Reproduce and fix the isolated fallback

**Files:**
- Modify: `tests/test_metadata.py`
- Modify: `src/tmuxcue/metadata.py`

**Interfaces:**
- Existing: `MetadataResolver.resolve(row, processes=None, hook_events=None) -> Metadata`.
- Extend: `MetadataResolver.codex_metadata(cwd, process, subject="", *, allow_cwd_fallback=True) -> tuple[str, str, str]`.
- Reuse: `create_thread_database`, `create_log_database`, and `create_goal_database` in `tests/test_metadata.py`.
- Exercise: `IndicatorDaemon.refresh_metadata`, `raw_state`, and `apply_targets`, with all tmux writes mocked.

- [ ] **Step 1: Add the regression fixture and tests**

Add these imports to `tests/test_metadata.py`:

```python
from dataclasses import replace
from unittest.mock import Mock, patch

from tmuxcue.core import Metadata, PaneRow, TerminalRow
from tmuxcue.service import IndicatorDaemon
```

Replace the existing narrower imports rather than duplicating them. Add the
following class before the file's `unittest.main()` guard:

```python
class CodexPaneIsolationTests(unittest.TestCase):
    old = "11111111-1111-1111-1111-111111111111"
    current = "22222222-2222-2222-2222-222222222222"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / "home"
        self.codex = self.home / ".codex"
        self.proc = Path(temporary.name) / "proc"
        self.fd = self.proc / "123/fd"
        self.fd.mkdir(parents=True)
        (self.codex / "sessions").mkdir(parents=True)
        environment = patch.dict(os.environ, {"CODEX_HOME": str(self.codex)})
        environment.start()
        self.addCleanup(environment.stop)
        self.database = self.codex / "state_1.sqlite"
        create_thread_database(self.database, [])
        self.row = PaneRow(
            "%1", "s:0", "codex", "idle", "/repo", "",
            "/dev/pts/1", 123, 10_000, "codex",
        )
        self.resolver = MetadataResolver(home=self.home, proc_root=self.proc)
        self.old_rollout = self.add_thread(self.old)
        self.old_metadata = Metadata("codex", "old-model", "Old thread", self.old)

    def add_thread(
        self, session_id, *, created=1_000, updated=2_000,
        model="old-model", topic="Old thread",
    ):
        rollout = self.codex / "sessions" / f"rollout-{session_id}.jsonl"
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "insert into threads values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id, created, updated, model, topic, topic,
                    "/repo", 0, "user", str(rollout),
                ),
            )
        return rollout

    def make_daemon(self):
        daemon = IndicatorDaemon()
        daemon.metadata_resolver = self.resolver
        daemon.tmux = Mock()
        daemon.tmux.set_many.return_value = True
        daemon.last_scan = 10_010.0
        return daemon

    def test_unbound_pane_rejects_one_old_same_cwd_thread(self):
        self.assertEqual(
            self.resolver.resolve(self.row),
            Metadata("codex", "", "repo", ""),
        )

    def test_unbound_pane_rejects_latest_of_multiple_old_threads(self):
        self.add_thread(
            self.current, updated=10_020_000,
            model="other-model", topic="Other thread",
        )
        row = replace(self.row, subject="Current UI")
        self.assertEqual(
            self.resolver.resolve(row),
            Metadata("codex", "", "Current UI", ""),
        )

    def test_unbound_pane_does_not_select_a_future_thread(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute("delete from threads")
        self.add_thread(
            self.current, created=11_000_000, updated=11_000_000,
            model="other-model", topic="Future thread",
        )
        self.assertEqual(self.resolver.resolve(self.row).session_id, "")

    def test_cwd_fallback_remains_optional_for_direct_callers(self):
        process = (self.row.pid, self.row.started_at, self.row.cmdline)
        expected = ("old-model", "Old thread", self.old)
        self.assertEqual(
            self.resolver.codex_metadata("/repo", process), expected,
        )
        self.assertEqual(
            self.resolver.codex_metadata(
                "/repo", process, allow_cwd_fallback=True,
            ),
            expected,
        )
        self.assertEqual(
            self.resolver.codex_metadata(
                "/repo", process, allow_cwd_fallback=False,
            ),
            ("", "", ""),
        )

    def test_strict_lookup_without_process_stays_unbound(self):
        self.assertEqual(
            self.resolver.codex_metadata(
                "/repo", None, allow_cwd_fallback=False,
            ),
            ("", "", ""),
        )

    def test_direct_terminal_keeps_old_cwd_fallback(self):
        row = TerminalRow(
            123, "/dev/pts/1", "codex", "idle", "/repo", 10_000, "codex",
        )
        self.assertEqual(self.resolver.resolve(row), self.old_metadata)

    def test_nearby_creation_still_binds_current_pane(self):
        self.add_thread(
            self.current, created=10_001_000, updated=10_002_000,
            model="current-model", topic="Current thread",
        )
        self.assertEqual(
            self.resolver.resolve(self.row),
            Metadata("codex", "current-model", "Current thread", self.current),
        )

    def test_old_explicit_bindings_beat_a_new_same_cwd_thread(self):
        self.add_thread(
            self.current, created=10_001_000, updated=10_002_000,
            model="current-model", topic="Current thread",
        )
        for binding in ("command", "rollout", "writer_lock", "log", "title"):
            with self.subTest(binding=binding):
                row = self.row
                link = self.fd / "40"
                log = self.codex / "logs_2.sqlite"
                if binding == "command":
                    row = replace(row, cmdline=f"codex resume {self.old}")
                elif binding == "rollout":
                    self.old_rollout.touch()
                    link.symlink_to(self.old_rollout)
                elif binding == "writer_lock":
                    lock = self.codex / "thread-writer-locks" / f"{self.old}.lock"
                    lock.parent.mkdir()
                    lock.touch()
                    link.symlink_to(lock)
                elif binding == "log":
                    create_log_database(
                        log,
                        [
                            (10_001, 0, self.old, "pid:123:current"),
                            (10_002, 0, self.current, "pid:999:other"),
                        ],
                    )
                elif binding == "title":
                    row = replace(row, subject="Old thread")
                try:
                    self.assertEqual(self.resolver.resolve(row), self.old_metadata)
                finally:
                    link.unlink(missing_ok=True)
                    log.unlink(missing_ok=True)

    def test_missing_or_malformed_database_stays_unbound(self):
        self.database.unlink()
        self.assertEqual(self.resolver.resolve(self.row).session_id, "")
        self.database.write_text("not a database", encoding="utf-8")
        self.assertEqual(self.resolver.resolve(self.row).session_id, "")

    def test_old_goals_and_hooks_cannot_change_unbound_pane(self):
        goals = self.codex / "goals_1.sqlite"
        create_goal_database(goals, [(self.old, "blocked", 10_000_000)])
        bound = replace(
            self.row, pane="%2", window="s:1", tty="/dev/pts/2",
            pid=124, subject="Old thread",
        )
        for goal in ("blocked", "active"):
            with sqlite3.connect(goals) as connection:
                connection.execute("update thread_goals set status=?", (goal,))
            for hook in (None, "approval", "running", "ended"):
                with self.subTest(goal=goal, hook=hook):
                    daemon = self.make_daemon()
                    daemon.rows = {self.row.window: self.row, bound.window: bound}
                    daemon.refresh_metadata_targets(
                        set(daemon.rows), daemon.last_scan,
                    )
                    events = {}
                    if hook is not None:
                        events[("codex", self.old)] = {
                            "state": hook, "timestamp": daemon.last_scan + 0.5,
                        }
                    with patch("builtins.print"):
                        daemon.apply_targets(
                            set(daemon.rows), events, daemon.last_scan + 1,
                        )
                    self.assertEqual(daemon.applied[self.row.window][0], "done")
                    self.assertEqual(daemon.metadata[self.row.window].session_id, "")
                    self.assertNotIn("Old thread", daemon.applied[self.row.window][2])
                    self.assertEqual(daemon.metadata[bound.window], self.old_metadata)
                    if hook is None:
                        self.assertEqual(
                            daemon.applied[bound.window][0],
                            "approval" if goal == "blocked" else "running",
                        )

    def test_unbound_pane_keeps_native_state(self):
        for native, visual in (
            ("idle", "done"), ("working", "running"), ("blocked", "approval"),
        ):
            with self.subTest(native=native):
                row = replace(self.row, state=native)
                daemon = self.make_daemon()
                daemon.rows = {row.window: row}
                daemon.refresh_metadata(row.window, row, daemon.last_scan)
                with patch("builtins.print"):
                    daemon.apply_targets({row.window}, {}, daemon.last_scan + 1)
                self.assertEqual(daemon.applied[row.window][0], visual)
                self.assertEqual(daemon.metadata[row.window].session_id, "")

    def test_normal_refresh_binds_new_thread_and_invalidates_runtime_cache(self):
        daemon = self.make_daemon()
        target = self.row.window
        initial = daemon.refresh_metadata(target, self.row, daemon.last_scan)
        self.assertEqual(initial.session_id, "")
        daemon.raw_state(target, self.row, initial, daemon.last_scan)
        self.assertIn(target, daemon.runtime_states)
        self.add_thread(
            self.current, created=10_001_000, updated=10_002_000,
            model="current-model", topic="Current thread",
        )
        current = daemon.refresh_metadata(target, self.row, daemon.last_scan + 1)
        self.assertEqual(
            current,
            Metadata("codex", "current-model", "Current thread", self.current),
        )
        self.assertNotIn(target, daemon.runtime_states)
        create_goal_database(
            self.codex / "goals_1.sqlite",
            [(self.current, "active", 10_010_000)],
        )
        self.assertEqual(
            daemon.raw_state(target, self.row, current, daemon.last_scan + 1),
            "working",
        )
```

- [ ] **Step 2: Run the regressions against the unchanged implementation**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests \
  python3 -m unittest test_metadata.CodexPaneIsolationTests -v
```

Expected: assertions expose old-thread metadata leaking into new panes, and
the new keyword argument is initially unsupported. The compatibility tests
for existing bindings should already pass.

- [ ] **Step 3: Make the minimal production change**

In `resolve()`, replace the Codex call with:

```python
model, topic, session_id = self.codex_metadata(
    cwd,
    process,
    row.subject,
    allow_cwd_fallback=not isinstance(row, PaneRow),
)
```

Change the signature to:

```python
def codex_metadata(
    self,
    cwd: str,
    process: ProcessInfo | None,
    subject: str = "",
    *,
    allow_cwd_fallback: bool = True,
) -> tuple[str, str, str]:
```

Immediately after the existing `if nearby:` selection and before its final
`else:` branch, add:

```python
elif not allow_cwd_fallback:
    return "", "", ""
```

- [ ] **Step 4: Run focused and full tests**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests \
  python3 -m unittest test_metadata.CodexPaneIsolationTests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m unittest discover -s tests -q
git diff --check
```

Expected: all 12 new tests and all existing 123 tests pass; no whitespace
errors. Inspect the production diff to ensure only the opt-in guard changed.

- [ ] **Step 5: Commit the tested fix**

```bash
git add src/tmuxcue/metadata.py tests/test_metadata.py
git commit -m "fix: keep unbound Codex panes from borrowing old threads"
```

### Task 2: Document the compatibility boundary

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:**
- Consumes: the resolver behavior from Task 1.
- Produces: accurate user-facing and architecture descriptions, without a new configuration knob.

- [ ] **Step 1: Add this README section before Codex Goal mode**

```markdown
## Codex session binding

Codex tmux panes do not borrow an old thread solely because it shares their
working directory. If no earlier binding path selects a thread, TmuxCue
keeps the pane unbound and uses its native subject or directory name instead
of another thread's model, topic, Goal, or hooks. Normal metadata refresh
binds the current thread when it becomes discoverable.

Command-line IDs, open rollout/writer-lock files, process logs, unique native
title matches, and the existing creation-time-proximity heuristic retain
their selection order. Proximity remains a heuristic, not an exact process
binding. Regular-terminal fallback and Claude/Gemini behavior are unchanged.
```

- [ ] **Step 2: Add this architecture paragraph before Output safety**

```markdown
Codex metadata resolution distinguishes earlier binding evidence from its
final cwd-only fallback. For tmux panes, that final fallback is disabled:
when command-line identity, open rollout/writer-lock files, process logs,
unique native titles, and the existing creation-time-proximity heuristic
all fail to select a candidate, the pane stays unbound. An empty session ID
cannot select another thread's runtime snapshot or hooks. The existing
native subject/directory label and native pane state remain available.
Direct-terminal resolution keeps the legacy final fallback. This restriction
does not make the remaining creation-time heuristic an exact binding.
```

- [ ] **Step 3: Review and commit documentation**

```bash
git diff --check
git add README.md docs/ARCHITECTURE.md
git commit -m "docs: explain isolated Codex pane fallback"
```

### Task 3: Stage and safely deploy the code-only update

**Files:**
- No tracked installation-script or configuration changes.
- A new local release under `~/.local/share/tmuxcue/releases/`.

**Interfaces:**
- Consumes: tested `src/tmuxcue/metadata.py` and the existing installed runtime.
- Produces: active installed fix, unchanged tmux/AI identities, and private local verification snapshots.

- [ ] **Step 1: Stage the code and run the full suite with the installed interpreter**

```bash
set -euo pipefail
ROOT="$(pwd)"
BASE="$HOME/.local/share/tmuxcue"
STAGE="$BASE/releases/$(date -u +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD)"
mkdir -m 700 "$STAGE"
cp -a "$BASE/current/." "$STAGE/"
cp "$ROOT/src/tmuxcue/metadata.py" "$STAGE/src/tmuxcue/metadata.py"
printf '%s\n' "$STAGE"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$STAGE/src" \
  "$STAGE/python" -m unittest discover -s "$ROOT/tests" -q
```

Retain the printed `STAGE` value for the following deployment transaction.
Verify that the staged interpreter symlink is unchanged, and that the only
changed non-bytecode file compared with the previous release is
`src/tmuxcue/metadata.py`.

- [ ] **Step 2: Snapshot, atomically activate, verify, and roll back on failure**

In the shell holding `ROOT`, `BASE`, and `STAGE` from Step 1:

```bash
set -euo pipefail
PREVIOUS="$(readlink "$BASE/current")"
AUDIT="$(mktemp -d "${TMPDIR:-/tmp}/tmuxcue-isolation-audit.XXXXXX")"
SWITCHED=0
finish() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ] && [ "$SWITCHED" -eq 1 ]; then
        ln -s "$PREVIOUS" "$BASE/.isolation-rollback-$$"
        mv -Tf "$BASE/.isolation-rollback-$$" "$BASE/current"
        systemctl --user restart tmuxcue.service
    fi
    printf 'Audit directory: %s\n' "$AUDIT"
    exit "$status"
}
trap finish EXIT
snapshot() {
    destination="$1"
    tmux display-message -p '#{pid}' > "$destination.server"
    tmux list-panes -a \
      -F '#{session_id}:#{window_id}:#{pane_id}:#{pane_pid}:#{pane_tty}' \
      | sort > "$destination.panes"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$STAGE/src" \
      "$STAGE/python" -m tmuxcue __process-snapshot > "$destination.agents"
    {
        tmux show-options -g
        tmux show-window-options -g
    } > "$destination.options"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$STAGE/src" \
      "$STAGE/python" - > "$destination.config" <<'PY'
import hashlib
from pathlib import Path
from tmuxcue.settings import (
    config_path, state_path, gemini_settings_registry_path,
    managed_gemini_settings_paths,
)

home = Path.home()
paths = {
    home / ".tmux.conf",
    home / ".codex/config.toml",
    home / ".codex/hooks.json",
    home / ".claude/settings.json",
    home / ".gemini/settings.json",
    home / ".vscode-server/data/Machine/settings.json",
    home / ".vscode-server-insiders/data/Machine/settings.json",
    home / ".config/systemd/user/tmuxcue.service",
    home / ".local/bin/tmuxcue",
    home / ".local/bin/tmuxcue-hook",
    home / ".local/bin/tmuxcue-service",
    config_path(),
    state_path(),
    gemini_settings_registry_path(),
    *managed_gemini_settings_paths(),
}
for path in sorted(paths):
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"
    print(f"{path}\t{fingerprint}")
PY
}
snapshot "$AUDIT/before"
ln -s "$STAGE" "$BASE/.isolation-current-$$"
mv -Tf "$BASE/.isolation-current-$$" "$BASE/current"
SWITCHED=1
systemctl --user restart tmuxcue.service
sleep 2
systemctl --user is-active --quiet tmuxcue.service
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$BASE/current/src:$ROOT/tests" \
  "$BASE/current/python" -m unittest test_metadata.CodexPaneIsolationTests -q
snapshot "$AUDIT/after"
for identity in server panes agents options config; do
    diff -u "$AUDIT/before.$identity" "$AUDIT/after.$identity"
done
```

Observe the live target's native and managed state using read-only commands.
Do not send keys
to create a test condition. If a session changes naturally during the audit,
investigate the difference rather than claiming identity preservation.

- [ ] **Step 3: Record the verified deployment**

Update the approved specification's status to implemented and deployed.
Check off completed steps here and append actual test counts, service health,
identity counts, and observed live-state results. Keep private snapshots out
of Git.

```bash
git add docs/superpowers/specs/2026-10-22-codex-unbound-pane-isolation-design.md \
  docs/superpowers/plans/2026-10-22-codex-unbound-pane-isolation.md
git commit -m "docs: record verified Codex pane isolation deployment"
```

### Task 4: Deliver to the existing remote

**Files:** No further source changes.

**Interfaces:**
- Consumes: clean, tested, deployed local commits.
- Produces: identical local and remote delivered commits.

- [ ] **Step 1: Verify remote ancestry and push normally**

```bash
test -z "$(git status --porcelain)"
git fetch --no-tags origin main
git merge-base --is-ancestor origin/main HEAD
git push origin HEAD:main
test "$(git rev-parse HEAD)" = \
  "$(git ls-remote --heads origin main | cut -f1)"
```

If ancestry fails, inspect and integrate the remote changes before pushing;
never force-push. Report the delivered commit only after the final equality
check succeeds.

## Plan Self-Review

- Spec criteria 1–3 and 6: single/multiple/future candidate regressions and
  the two-pane Goal/hook test.
- Criterion 4: all five binding variants in real synthetic file/log fixtures.
- Criteria 5 and 7: nearby-thread and direct-terminal compatibility tests.
- Criterion 8: normal metadata refresh and runtime-cache invalidation test.
- Criterion 9: focused and full source/staged/installed test runs.
- Criterion 10: README and architecture text in Task 2.
- Safety and delivery: atomic code-only deployment, identity/options
  snapshots, rollback, and normal fast-forward push.
- All added fixtures are synthetic; no new runtime dependency or database
  field is introduced.
