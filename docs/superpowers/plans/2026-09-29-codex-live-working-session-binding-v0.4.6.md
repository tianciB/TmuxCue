# Codex Live Working Session Binding v0.4.6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind each resumed Codex process to its own user thread and keep a pane running when a newer live tmux scan still shows explicit work.

**Architecture:** `MetadataResolver` will use the Codex process PID and start time to identify the current `process_uuid` in `logs_2.sqlite`, then accept its latest thread only when that thread is already in the same-cwd top-level user candidate set. If Codex has rotated away that process's thread-bearing log row, `TmuxDetector` will provide an exact native-title thread-name hint that is accepted only when it uniquely matches the same candidate set. `IndicatorDaemon` will treat a newer tmux `working` scan as stronger than an older runtime `task_complete`, while retaining authoritative abort and approval behavior.

**Tech Stack:** Python 3.10+, SQLite read-only URIs, `/proc`, Python `unittest`, Bash, tmux, systemd user services.

## Global Constraints

- Never send keys, input bytes, or signals to a tmux pane or AI process.
- Never run tmux `kill-*`, `new-session`, `attach`, `respawn`, or `source-file`.
- Restart only `tmuxcue.service` during installation.
- Use read-only process, Codex log database, thread database, and rollout access.
- Accept PID-log thread IDs only from the same cwd, unarchived, top-level user candidate set.
- Keep explicit command-line IDs and open rollout/writer-lock evidence ahead of PID-log evidence.
- Accept native-title evidence only as a unique exact match after removing the spinner and repository suffix.
- Keep runtime `turn_aborted` authoritative over a residual working pane.
- Add no dependency outside the Python standard library.
- Publish the complete fix as version `0.4.6`.

---

### Task 1: Bind Codex Processes Through Process Logs and Native Titles

**Files:**
- Modify: `tests/test_metadata.py:13-276`
- Modify: `tests/test_detector.py:33-61`
- Modify: `src/tmuxcue/metadata.py:211-324`
- Modify: `src/tmuxcue/detector.py:61-103`

**Interfaces:**
- Consumes: `ProcessInfo = tuple[int, int, str]`, `MetadataResolver.codex_home`, `MetadataResolver.proc_root`, and the existing same-cwd user-thread query.
- Produces: `MetadataResolver.codex_logged_session_id(pid: int, started_at: int) -> str`.
- Produces: `TmuxDetector.title_subject(title: str) -> str`.
- Produces: selection order `command-line UUID -> open rollout/lock -> PID-log thread -> unique native-title name -> existing heuristics`.

- [ ] **Step 1: Add a reusable Codex log database fixture**

Add this helper below `create_thread_database()` in `tests/test_metadata.py`:

```python
def create_log_database(
    path: Path, rows: list[tuple[int, int, str | None, str]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        """
        create table logs (
            id integer primary key autoincrement,
            ts integer not null,
            ts_nanos integer not null,
            thread_id text,
            process_uuid text not null
        )
        """
    )
    connection.executemany(
        """
        insert into logs (ts, ts_nanos, thread_id, process_uuid)
        values (?, ?, ?, ?)
        """,
        rows,
    )
    connection.commit()
    connection.close()
```

- [ ] **Step 2: Add PID binding and fallback regression tests**

Add these tests to `CodexMetadataTests`:

```python
    def test_process_log_binds_same_cwd_resume_to_user_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            codex = home / ".codex"
            codex.mkdir(parents=True)
            proc = root / "proc"
            (proc / "123").mkdir(parents=True)
            create_thread_database(
                codex / "state_1.sqlite",
                [
                    (
                        self.correct,
                        1_000,
                        6_000,
                        "model",
                        "correct title",
                        "correct topic",
                        "/repo",
                        0,
                        "user",
                        "/tmp/correct.jsonl",
                    ),
                    (
                        self.wrong,
                        5_000,
                        9_000,
                        "model",
                        "wrong title",
                        "wrong topic",
                        "/repo",
                        0,
                        "user",
                        "/tmp/wrong.jsonl",
                    ),
                ],
            )
            create_log_database(
                codex / "logs_2.sqlite",
                [
                    (5, 0, None, "pid:123:current"),
                    (6, 0, self.correct, "pid:123:current"),
                    (7, 0, self.wrong, "pid:999:other"),
                ],
            )
            resolver = MetadataResolver(home=home, proc_root=proc)

            model, topic, session_id = resolver.codex_metadata(
                "/repo", (123, 5, "codex resume")
            )

            self.assertEqual(model, "model")
            self.assertEqual(topic, "correct topic")
            self.assertEqual(session_id, self.correct)

    def test_process_log_does_not_reuse_an_old_pid_incarnation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            codex = home / ".codex"
            codex.mkdir(parents=True)
            proc = root / "proc"
            (proc / "123").mkdir(parents=True)
            create_log_database(
                codex / "logs_2.sqlite",
                [
                    (90, 0, self.correct, "pid:123:old"),
                    (100, 0, None, "pid:123:current"),
                ],
            )
            resolver = MetadataResolver(home=home, proc_root=proc)

            self.assertEqual(resolver.codex_logged_session_id(123, 100), "")

    def test_process_log_thread_must_be_a_user_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            codex = home / ".codex"
            codex.mkdir(parents=True)
            proc = root / "proc"
            (proc / "123").mkdir(parents=True)
            create_thread_database(
                codex / "state_1.sqlite",
                [
                    (
                        self.wrong,
                        5_000,
                        9_000,
                        "model",
                        "wrong title",
                        "wrong topic",
                        "/repo",
                        0,
                        "user",
                        "/tmp/wrong.jsonl",
                    ),
                    (
                        self.subagent,
                        1_000,
                        10_000,
                        "model",
                        "child title",
                        "child topic",
                        "/repo",
                        0,
                        "subagent",
                        "/tmp/child.jsonl",
                    ),
                ],
            )
            create_log_database(
                codex / "logs_2.sqlite",
                [(6, 0, self.subagent, "pid:123:current")],
            )
            resolver = MetadataResolver(home=home, proc_root=proc)

            _, topic, session_id = resolver.codex_metadata(
                "/repo", (123, 5, "codex resume")
            )

            self.assertEqual(topic, "wrong topic")
            self.assertEqual(session_id, self.wrong)

    def test_malformed_process_log_database_uses_existing_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            codex = home / ".codex"
            codex.mkdir(parents=True)
            proc = root / "proc"
            (proc / "123").mkdir(parents=True)
            create_thread_database(
                codex / "state_1.sqlite",
                [
                    (
                        self.wrong,
                        5_000,
                        9_000,
                        "model",
                        "wrong title",
                        "wrong topic",
                        "/repo",
                        0,
                        "user",
                        "/tmp/wrong.jsonl",
                    ),
                ],
            )
            (codex / "logs_2.sqlite").write_text("not sqlite", encoding="utf-8")
            resolver = MetadataResolver(home=home, proc_root=proc)

            _, topic, session_id = resolver.codex_metadata(
                "/repo", (123, 5, "codex resume")
            )

            self.assertEqual(topic, "wrong topic")
            self.assertEqual(session_id, self.wrong)
```

Extend `test_open_user_rollout_beats_nearby_created_thread()` after creating
the state database so log evidence points at the wrong user thread:

```python
            create_log_database(
                codex / "logs_2.sqlite",
                [(6, 0, self.wrong, "pid:123:current")],
            )
```

Add this log-rotation fallback test to `CodexMetadataTests`:

```python
    def test_native_title_hint_binds_after_process_log_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            codex = home / ".codex"
            codex.mkdir(parents=True)
            proc = root / "proc"
            (proc / "123").mkdir(parents=True)
            create_thread_database(
                codex / "state_1.sqlite",
                [
                    (
                        self.correct,
                        1_000,
                        6_000,
                        "model",
                        "correct title",
                        "correct topic",
                        "/repo",
                        0,
                        "user",
                        "/tmp/correct.jsonl",
                    ),
                    (
                        self.wrong,
                        5_000,
                        9_000,
                        "model",
                        "wrong title",
                        "wrong topic",
                        "/repo",
                        0,
                        "user",
                        "/tmp/wrong.jsonl",
                    ),
                ],
            )
            create_log_database(
                codex / "logs_2.sqlite",
                [(6, 0, None, "pid:123:current")],
            )
            resolver = MetadataResolver(home=home, proc_root=proc)

            _, topic, session_id = resolver.codex_metadata(
                "/repo",
                (123, 5, "codex resume"),
                "correct topic",
            )

            self.assertEqual(topic, "correct topic")
            self.assertEqual(session_id, self.correct)
```

Add this detector test to `tests/test_detector.py`:

```python
    def test_native_title_subject_strips_spinner_and_repository(self):
        self.assertEqual(
            self.detector.title_subject("⠙ Exact thread name | repository"),
            "Exact thread name",
        )
        self.assertEqual(
            self.detector.title_subject("Exact thread name | repository"),
            "Exact thread name",
        )
```

- [ ] **Step 3: Run the focused metadata tests and verify the new behavior fails**

Run:

```bash
PYTHONPATH=src:tests python -m unittest \
  test_metadata.CodexMetadataTests.test_process_log_binds_same_cwd_resume_to_user_thread \
  test_metadata.CodexMetadataTests.test_process_log_does_not_reuse_an_old_pid_incarnation \
  test_metadata.CodexMetadataTests.test_native_title_hint_binds_after_process_log_rotation \
  -v
```

Expected: `ERROR` because the new interfaces do not exist, or `FAIL` because
the same-cwd heuristic selects `self.wrong` instead of `self.correct`.

- [ ] **Step 4: Implement read-only PID-log session discovery**

Add this method after `codex_open_session_ids()` in
`src/tmuxcue/metadata.py`:

```python
    def codex_logged_session_id(self, pid: int, started_at: int) -> str:
        if pid <= 0:
            return ""
        process = self.proc_root / str(pid)
        try:
            if process.stat().st_uid != self.uid:
                return ""
        except OSError:
            return ""

        database = self.codex_home / "logs_2.sqlite"
        if not database.is_file():
            return ""

        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"file:{database}?mode=ro",
                uri=True,
                timeout=0.05,
            )
            process_row = connection.execute(
                """
                select process_uuid
                  from logs
                 where process_uuid glob ?
                   and ts >= ?
                 order by ts desc, ts_nanos desc, id desc
                 limit 1
                """,
                (f"pid:{pid}:*", max(0, started_at)),
            ).fetchone()
            if not process_row or not process_row[0]:
                return ""
            thread_row = connection.execute(
                """
                select thread_id
                  from logs
                 where process_uuid=?
                   and thread_id is not null
                   and thread_id != ''
                 order by ts desc, ts_nanos desc, id desc
                 limit 1
                """,
                (process_row[0],),
            ).fetchone()
        except sqlite3.Error:
            return ""
        finally:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass

        session_id = str(thread_row[0] or "") if thread_row else ""
        return session_id if UUID_RE.fullmatch(session_id) else ""
```

The first query identifies the current process incarnation rather than
trusting every historical row with a reused numeric PID. The second query
then follows only that exact `process_uuid`.

- [ ] **Step 5: Extract the exact native thread-name hint**

Add this method beside `title_is_working()` in `src/tmuxcue/detector.py`:

```python
    @classmethod
    def title_subject(cls, title: str) -> str:
        value = (title or "").strip()
        if cls.title_is_working(value):
            value = value[1:].lstrip()
        return value.rsplit(" | ", 1)[0].strip()
```

Change the `PaneRow` construction in `scan()` from an empty subject to:

```python
            row = PaneRow(
                pane,
                window,
                agent,
                state,
                cwd,
                self.title_subject(title),
                tty,
                pid,
                started_at,
                cmdline,
            )
```

- [ ] **Step 6: Insert exact process and title evidence into selection**

Change the `codex_metadata()` signature to:

```python
    def codex_metadata(
        self, cwd: str, process: ProcessInfo | None, subject: str = ""
    ) -> tuple[str, str, str]:
```

Pass the pane subject from `resolve()`:

```python
            model, topic, session_id = self.codex_metadata(
                cwd, process, row.subject
            )
```

Calculate the logged ID beside the existing command-line and open-file
evidence:

```python
        logged_id = (
            self.codex_logged_session_id(process[0], process[1])
            if process
            else ""
        )
```

After building `rows`, identify the candidate:

```python
        logged = next((row for row in rows if row["id"] == logged_id), None)
        named = [
            row
            for row in rows
            if subject and (row["name"] or "").strip() == subject.strip()
        ]
```

Keep the existing explicit and open-thread branches, then add the two new
branches:

```python
        if exact is not None:
            thread = exact
        elif open_threads:
            thread = max(
                open_threads,
                key=lambda row: row["updated_at_ms"] or 0,
            )
        elif logged is not None:
            thread = logged
        elif len(named) == 1:
            thread = named[0]
        else:
            nearby = [
                row for row in rows
                if abs((row["created_at_ms"] or 0) - started_ms) <= 180_000
            ]
```

- [ ] **Step 7: Run metadata and detector tests**

Run:

```bash
PYTHONPATH=src:tests python -m unittest test_metadata test_detector -v
```

Expected: all metadata tests pass, including open-rollout precedence, PID
incarnation isolation, candidate validation, malformed-log fallback, and
native-title recovery after log rotation.

- [ ] **Step 8: Commit the metadata binding fix**

Run:

```bash
git add \
  src/tmuxcue/detector.py \
  src/tmuxcue/metadata.py \
  tests/test_detector.py \
  tests/test_metadata.py
git commit -m "fix: bind Codex panes through process logs"
```

---

### Task 2: Prefer a Newer Live Working Scan Over Older Completion

**Files:**
- Modify: `tests/test_daemon.py:72-235`
- Modify: `src/tmuxcue/service.py:314-358`

**Interfaces:**
- Consumes: `IndicatorDaemon.last_scan`, runtime snapshot
  `(state: str | None, event_time: float)`, and `PaneRow.state`.
- Produces: `raw_state(...) == "working"` when pane evidence is newer than a
  runtime `idle` event.
- Preserves: runtime `aborted`, fresh approval hooks, and fresh completion
  events.

- [ ] **Step 1: Add live-working precedence tests**

Add these tests near the existing runtime precedence tests in
`tests/test_daemon.py`:

```python
    def test_fresh_working_scan_supersedes_older_task_complete_and_stop(self):
        daemon = IndicatorDaemon()
        now = time.time()
        completed_at = now - 5
        daemon.last_scan = now
        row = PaneRow(
            "%1",
            "s:0",
            "codex",
            "working",
            "/tmp",
            "",
            "/dev/pts/1",
            123,
            int(now - 100),
            "codex resume",
        )
        metadata = Metadata("codex", "model", "topic", "session")
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("idle", completed_at)
        )

        raw_state = daemon.raw_state("s:0", row, metadata, now)

        self.assertEqual(raw_state, "working")
        self.assertEqual(daemon.observation_time(row), now)
        self.assertEqual(
            daemon.resolve_state(
                row,
                raw_state,
                None,
                {"state": "done", "timestamp": completed_at},
                now,
            ),
            "running",
        )

    def test_newer_task_complete_still_beats_a_cached_working_scan(self):
        daemon = IndicatorDaemon()
        now = time.time()
        completed_at = now - 1
        daemon.last_scan = now - 5
        row = PaneRow(
            "%1",
            "s:0",
            "codex",
            "working",
            "/tmp",
            "",
            "/dev/pts/1",
            123,
            int(now - 100),
            "codex resume",
        )
        metadata = Metadata("codex", "model", "topic", "session")
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("idle", completed_at)
        )

        raw_state = daemon.raw_state("s:0", row, metadata, now)

        self.assertEqual(raw_state, "idle")
        self.assertEqual(
            daemon.resolve_state(
                row,
                raw_state,
                None,
                {"state": "done", "timestamp": completed_at},
                now,
            ),
            "done",
        )
```

Strengthen `test_turn_aborted_overrides_stale_running_ui_and_hook()` so the
abort is older than a fresh scan but remains authoritative:

```python
        now = time.time()
        daemon.last_scan = now
        aborted_at = now - 1
```

Pass `now` to both `raw_state()` and `resolve_state()` calls in that test.

- [ ] **Step 2: Run the new live-working test and verify it fails**

Run:

```bash
PYTHONPATH=src:tests python -m unittest \
  test_daemon.DaemonTests.test_fresh_working_scan_supersedes_older_task_complete_and_stop \
  -v
```

Expected: `FAIL` because current `raw_state()` returns `idle` rather than
`working`.

- [ ] **Step 3: Implement timestamp-aware idle precedence**

In `IndicatorDaemon.raw_state()`, replace the existing pre-process runtime
guard with:

```python
        if runtime_state is not None:
            if (
                isinstance(row, PaneRow)
                and row.state == "working"
                and runtime_state in {"idle", "aborted"}
                and event_time > 0
            ):
                if row.started_at > 0 and event_time < row.started_at:
                    self.raw_observation_times[target] = self.last_scan
                    return "working"
                if runtime_state == "idle" and self.last_scan > event_time:
                    self.raw_observation_times[target] = self.last_scan
                    return "working"
            observation_time = event_time or self.runtime_times.get(target, now)
```

Do not add the newer-scan exception for `aborted`.

- [ ] **Step 4: Run daemon and detector state tests**

Run:

```bash
PYTHONPATH=src:tests python -m unittest \
  test_daemon \
  test_detector \
  test_core \
  -v
```

Expected: all state, approval, abort, prompt-grace, and detector tests pass.

- [ ] **Step 5: Commit the state precedence fix**

Run:

```bash
git add src/tmuxcue/service.py tests/test_daemon.py
git commit -m "fix: keep newer live Codex work running"
```

---

### Task 3: Publish the Fix as TmuxCue 0.4.6

**Files:**
- Modify: `tests/test_cli.py:14-18`
- Modify: `src/tmuxcue/__init__.py:3`
- Modify: `pyproject.toml:7`

**Interfaces:**
- Consumes: the existing `__version__` API and CLI `version` command.
- Produces: package and CLI version `0.4.6`.

- [ ] **Step 1: Change the CLI test to require 0.4.6**

Change the version assertion in `tests/test_cli.py` to:

```python
        self.assertIn("TmuxCue 0.4.6", output.getvalue())
```

- [ ] **Step 2: Verify the version test fails**

Run:

```bash
PYTHONPATH=src:tests python -m unittest \
  test_cli.CliTests.test_version_and_help \
  -v
```

Expected: `FAIL` because the current output is `TmuxCue 0.4.5`.

- [ ] **Step 3: Update both authoritative version values**

Set `src/tmuxcue/__init__.py` to:

```python
__version__ = "0.4.6"
```

Set the project version in `pyproject.toml` to:

```toml
version = "0.4.6"
```

- [ ] **Step 4: Run the version test and complete regression suite**

Run:

```bash
PYTHONPATH=src:tests python -m unittest \
  test_cli.CliTests.test_version_and_help \
  -v
PYTHONPATH=src python -m unittest discover -s tests -v
git diff --check
```

Expected: the version test passes, the complete suite passes with more than
the baseline 62 tests, and `git diff --check` reports no errors.

- [ ] **Step 5: Commit the release version**

Run:

```bash
git add pyproject.toml src/tmuxcue/__init__.py tests/test_cli.py
git commit -m "chore: release version 0.4.6"
```

---

### Task 4: Install and Verify Without Disturbing Other Sessions

**Files:**
- Verify only: repository, installed release, systemd user service, tmux
  window options, and process snapshots.

**Interfaces:**
- Consumes: the tested installer and committed v0.4.6 repository state.
- Produces: an active v0.4.6 `tmuxcue.service` with unchanged tmux pane and
  AI-process PIDs.

- [ ] **Step 1: Require a clean committed worktree**

Run:

```bash
git status --short
```

Expected: no output.

- [ ] **Step 2: Record independent pre-install safety snapshots**

Run:

```bash
SAFETY=/tmp/tmuxcue-v046-safety
rm -rf "$SAFETY"
mkdir -m 700 "$SAFETY"
(pgrep -o -u "$(id -u)" tmux || true) > "$SAFETY/tmux.before"
tmux list-panes -a \
  -F '#{session_name}:#{window_index}.#{pane_index}:#{pane_pid}' \
  | sort > "$SAFETY/panes.before"
ps -eo uid=,pid=,comm= \
  | awk -v uid="$(id -u)" \
      '$1 == uid && ($3 == "codex" || $3 == "claude" || $3 == "claude-code") { print $2 ":" $3 }' \
  | sort > "$SAFETY/ai.before"
systemctl --user show tmuxcue.service -p MainPID --value \
  > "$SAFETY/service.before"
```

- [ ] **Step 3: Run the tested safety-preserving installer**

Run:

```bash
./install.sh
```

Expected:

```text
TmuxCue installed. Existing tmux and terminal AI processes were left untouched.
```

The installer may restart only `tmuxcue.service`; its built-in PID checks must
pass.

- [ ] **Step 4: Recreate snapshots and require exact process equality**

Run:

```bash
SAFETY=/tmp/tmuxcue-v046-safety
(pgrep -o -u "$(id -u)" tmux || true) > "$SAFETY/tmux.after"
tmux list-panes -a \
  -F '#{session_name}:#{window_index}.#{pane_index}:#{pane_pid}' \
  | sort > "$SAFETY/panes.after"
ps -eo uid=,pid=,comm= \
  | awk -v uid="$(id -u)" \
      '$1 == uid && ($3 == "codex" || $3 == "claude" || $3 == "claude-code") { print $2 ":" $3 }' \
  | sort > "$SAFETY/ai.after"
cmp "$SAFETY/tmux.before" "$SAFETY/tmux.after"
cmp "$SAFETY/panes.before" "$SAFETY/panes.after"
cmp "$SAFETY/ai.before" "$SAFETY/ai.after"
```

Expected: all three `cmp` commands succeed. The TmuxCue service PID is
expected to change because that is the only permitted restart.

- [ ] **Step 5: Verify installed version, service health, and PID-log mapping**

Run:

```bash
"$HOME/.local/bin/tmuxcue" version
systemctl --user is-active tmuxcue.service
sleep 5
PYTHONPATH="$HOME/.local/share/tmuxcue/current/src" \
  "$HOME/.local/share/tmuxcue/current/python" - <<'PY'
import time

from tmuxcue.core import PaneRow
from tmuxcue.service import IndicatorDaemon

daemon = IndicatorDaemon()
daemon.scan()
now = time.time()
daemon.refresh_metadata_targets(set(daemon.rows), now)
events = daemon.event_reader.poll(now)

checked = 0
for target, row in sorted(daemon.rows.items()):
    if not isinstance(row, PaneRow) or row.agent != "codex":
        continue
    metadata = daemon.metadata[target]
    logged = daemon.metadata_resolver.codex_logged_session_id(
        row.pid, row.started_at
    )
    if logged:
        assert metadata.session_id == logged, (
            target,
            metadata.session_id,
            logged,
        )
        checked += 1
    raw = daemon.raw_state(target, row, metadata, now)
    event = events.get((row.agent, metadata.session_id))
    resolved = daemon.resolve_state(
        row,
        raw,
        daemon.prompts.get(target),
        event,
        now,
    )
    if row.state == "working":
        runtime = daemon.runtime_states.get(target)
        runtime_at = daemon.runtime_event_times.get(target, 0)
        if runtime == "idle" and daemon.last_scan > runtime_at:
            assert raw == "working", (target, runtime, runtime_at, raw)
            assert resolved == "running", (target, raw, resolved)
    print(
        f"{target}: pid={row.pid} session={metadata.session_id} "
        f"detector={row.state} raw={raw} resolved={resolved} "
        f"topic={metadata.topic!r}"
    )

assert checked >= 1, "no live Codex process exposed PID-log thread evidence"
PY
```

Expected:

```text
TmuxCue 0.4.6
active
```

Every Codex process with PID-log evidence must resolve to that exact thread.
Any pane whose newer live scan conflicts with an older runtime idle event must
resolve to `running`.

- [ ] **Step 6: Verify service stability and inspect only its own journal**

Run:

```bash
PID_BEFORE="$(systemctl --user show tmuxcue.service -p MainPID --value)"
RESTARTS_BEFORE="$(systemctl --user show tmuxcue.service -p NRestarts --value)"
sleep 5
test "$PID_BEFORE" = \
  "$(systemctl --user show tmuxcue.service -p MainPID --value)"
test "$RESTARTS_BEFORE" = \
  "$(systemctl --user show tmuxcue.service -p NRestarts --value)"
journalctl --user -u tmuxcue.service --since '-2 minutes' --no-pager \
  | tail -200
```

Expected: the service remains active with no additional restart and no
traceback. Do not attach to or send input to any agent pane during
verification.

## Self-Review

- Task 1 covers exact same-cwd PID binding, PID reuse, other-PID isolation,
  candidate validation, malformed-log fallback, open-file precedence, and
  exact native-title recovery after process-log rotation.
- Task 2 covers newer live work, newer completion, abort authority, approval
  regressions, and detector regressions.
- Task 3 publishes one consistent public version and runs the complete test
  suite.
- Task 4 uses the existing safety installer, independently compares tmux and
  AI process PIDs, restarts only TmuxCue, and performs read-only live
  verification.
- Method names and signatures are consistent:
  `codex_logged_session_id(pid: int, started_at: int) -> str`.
- No task writes to Codex databases, rollouts, panes, or agent processes.
