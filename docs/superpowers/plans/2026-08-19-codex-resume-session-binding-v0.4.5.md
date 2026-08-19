# Codex Resume Session Binding v0.4.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind interactive `codex resume` panes to their exact open user rollout, prevent pre-process runtime events from overriding live work, and install TmuxCue 0.4.5 without disturbing existing conversations.

**Architecture:** `MetadataResolver` reads only process FD symlink targets under the configured Codex home, intersects discovered UUIDs with user-thread database rows, and prefers that process-local evidence over creation-time heuristics. `IndicatorDaemon` separately rejects runtime completion events that predate the current foreground process.

**Tech Stack:** Python 3.10+, SQLite, `/proc`, Python `unittest`, Bash, tmux, systemd user services.

## Global Constraints

- Never send keys, input bytes, or signals to a tmux pane or AI process.
- Never run tmux `kill-*`, `new-session`, `attach`, `respawn`, or
  `source-file`.
- Restart only `tmuxcue.service` during installation.
- Use read-only process FD and SQLite access.
- Accept session paths only below the configured Codex home.
- Keep subagent rollouts out of main-pane selection.
- Publish the code version as `0.4.5`.

---

### Task 1: Add Exact Open-Rollout Metadata Tests

**Files:**
- Modify: `tests/test_metadata.py`
- Test: `tests/test_metadata.py`

**Interfaces:**
- Consumes: `MetadataResolver.codex_metadata(cwd, process)`.
- Produces: regression coverage for open rollout and writer-lock UUIDs.

- [ ] **Step 1: Add a reusable thread database helper**

Add a helper in `tests/test_metadata.py` that creates a `threads` table with
the columns consumed by `codex_metadata()`:

```python
def create_thread_database(path: Path, rows: list[tuple]) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        create table threads (
            id text primary key,
            created_at_ms integer,
            updated_at_ms integer,
            model text,
            title text,
            name text,
            cwd text,
            archived integer,
            thread_source text,
            rollout_path text
        )
        """
    )
    connection.executemany(
        "insert into threads values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    connection.commit()
    connection.close()
```

- [ ] **Step 2: Add the failing interactive-resume test**

Add:

```python
class CodexMetadataTests(unittest.TestCase):
    correct = "11111111-1111-1111-1111-111111111111"
    wrong = "22222222-2222-2222-2222-222222222222"
    subagent = "33333333-3333-3333-3333-333333333333"

    def test_open_user_rollout_beats_nearby_created_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            codex = home / ".codex"
            proc = root / "proc"
            fd = proc / "123/fd"
            fd.mkdir(parents=True)
            sessions = codex / "sessions/2026/08/19"
            sessions.mkdir(parents=True)
            correct_rollout = sessions / f"rollout-current-{self.correct}.jsonl"
            subagent_rollout = sessions / f"rollout-child-{self.subagent}.jsonl"
            correct_rollout.write_text("", encoding="utf-8")
            subagent_rollout.write_text("", encoding="utf-8")
            (fd / "40").symlink_to(correct_rollout)
            (fd / "41").symlink_to(subagent_rollout)

            create_thread_database(
                codex / "state_1.sqlite",
                [
                    (
                        self.correct, 1_000, 9_000, "model", "correct title",
                        "correct topic", "/repo", 0, "user", str(correct_rollout),
                    ),
                    (
                        self.wrong, 5_000, 6_000, "model", "wrong title",
                        "wrong topic", "/repo", 0, "user", "/tmp/wrong.jsonl",
                    ),
                    (
                        self.subagent, 5_100, 10_000, "model", "child title",
                        "child topic", "/repo", 0, "subagent",
                        str(subagent_rollout),
                    ),
                ],
            )
            resolver = MetadataResolver(home=home, proc_root=proc)

            model, topic, session_id = resolver.codex_metadata(
                "/repo", (123, 5, "codex resume")
            )

            self.assertEqual(model, "model")
            self.assertEqual(topic, "correct topic")
            self.assertEqual(session_id, self.correct)
```

- [ ] **Step 3: Add the outside-Codex-home rejection test**

Add:

```python
    def test_rollout_outside_codex_home_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            codex = home / ".codex"
            codex.mkdir(parents=True)
            proc = root / "proc"
            fd = proc / "123/fd"
            fd.mkdir(parents=True)
            outside = root / f"rollout-current-{self.correct}.jsonl"
            outside.write_text("", encoding="utf-8")
            (fd / "40").symlink_to(outside)
            create_thread_database(
                codex / "state_1.sqlite",
                [
                    (
                        self.correct, 1_000, 9_000, "model", "correct title",
                        "correct topic", "/repo", 0, "user", str(outside),
                    ),
                    (
                        self.wrong, 5_000, 6_000, "model", "wrong title",
                        "wrong topic", "/repo", 0, "user", "/tmp/wrong.jsonl",
                    ),
                ],
            )
            resolver = MetadataResolver(home=home, proc_root=proc)

            _, topic, session_id = resolver.codex_metadata(
                "/repo", (123, 5, "codex resume")
            )

            self.assertEqual(topic, "wrong topic")
            self.assertEqual(session_id, self.wrong)
```

- [ ] **Step 4: Run the tests and confirm failure**

Run:

```bash
PYTHONPATH=src:tests "$HOME/anaconda3/bin/python3" \
  -m unittest \
  test_metadata.CodexMetadataTests \
  -v
```

Expected: failure because open process rollout discovery is not implemented.

### Task 2: Implement Process-Local Codex Session Discovery

**Files:**
- Modify: `src/tmuxcue/metadata.py`
- Test: `tests/test_metadata.py`

**Interfaces:**
- Produces: `MetadataResolver.codex_open_session_ids(pid: int) -> set[str]`.
- Updates: `codex_metadata()` selection priority.

- [ ] **Step 1: Add `codex_open_session_ids`**

Implement a read-only FD scan that:

```python
def codex_open_session_ids(self, pid: int) -> set[str]:
    if pid <= 0:
        return set()
    process = self.proc_root / str(pid)
    try:
        codex_home = self.codex_home.resolve()
    except OSError:
        codex_home = self.codex_home
    try:
        if process.stat().st_uid != self.uid:
            return set()
        entries = list((process / "fd").iterdir())
    except OSError:
        return set()

    session_ids: set[str] = set()
    for entry in entries:
        try:
            target = Path(
                os.readlink(entry).removesuffix(" (deleted)")
            ).resolve()
            relative = target.relative_to(codex_home)
        except (OSError, ValueError):
            continue
        if not relative.parts:
            continue
        if relative.parts[0] == "sessions" and target.suffix == ".jsonl":
            match = UUID_RE.search(target.name)
        elif (
            relative.parts[0] == "thread-writer-locks"
            and target.suffix == ".lock"
        ):
            match = UUID_RE.fullmatch(target.stem)
        else:
            match = None
        if match:
            session_ids.add(match.group(0))
    return session_ids
```

- [ ] **Step 2: Prefer matching open user threads**

In `codex_metadata()`:

```python
pid = process[0] if process else 0
open_session_ids = self.codex_open_session_ids(pid)
```

After explicit command-line matching and before the nearby-thread heuristic:

```python
open_threads = [row for row in rows if row["id"] in open_session_ids]
if exact is not None:
    thread = exact
elif open_threads:
    thread = max(open_threads, key=lambda row: row["updated_at_ms"] or 0)
else:
    # existing nearby and eligible fallback
```

- [ ] **Step 3: Run metadata tests**

Run:

```bash
PYTHONPATH=src:tests "$HOME/anaconda3/bin/python3" \
  -m unittest test_metadata -v
```

Expected: all metadata tests pass.

### Task 3: Add the Pre-Process Runtime Safety Guard

**Files:**
- Modify: `tests/test_daemon.py`
- Modify: `src/tmuxcue/service.py`
- Test: `tests/test_daemon.py`

**Interfaces:**
- Consumes: `PaneRow.started_at` and runtime event timestamp.
- Produces: live pane work when runtime completion belongs to an older process.

- [ ] **Step 1: Add the failing daemon regression**

Add:

```python
    def test_runtime_event_before_process_start_is_ignored(self):
        daemon = IndicatorDaemon()
        daemon.last_scan = 110
        row = PaneRow(
            "%1", "s:0", "codex", "working", "/tmp", "", "/dev/pts/1",
            123, 100, "codex resume",
        )
        metadata = Metadata("codex", "model", "topic", "session")
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("aborted", 90)
        )

        self.assertEqual(
            daemon.raw_state("s:0", row, metadata, 110),
            "working",
        )
        self.assertEqual(daemon.observation_time(row), 110)
```

Keep the existing newer-aborted-event test unchanged.

- [ ] **Step 2: Confirm the regression fails**

Run:

```bash
PYTHONPATH=src:tests "$HOME/anaconda3/bin/python3" \
  -m unittest \
  test_daemon.DaemonTests.test_runtime_event_before_process_start_is_ignored \
  -v
```

Expected: `FAIL`; old code returns `done`.

- [ ] **Step 3: Ignore stale pre-process completion**

In `IndicatorDaemon.raw_state()`, before the normal runtime-state return
branches, add:

```python
            if (
                isinstance(row, PaneRow)
                and row.state == "working"
                and runtime_state in {"idle", "aborted"}
                and event_time > 0
                and row.started_at > 0
                and event_time < row.started_at
            ):
                self.raw_observation_times[target] = self.last_scan
                return "working"
```

- [ ] **Step 4: Run daemon tests**

Run:

```bash
PYTHONPATH=src:tests "$HOME/anaconda3/bin/python3" \
  -m unittest test_daemon -v
```

Expected: all daemon tests pass, including the existing newer-aborted-event
override.

### Task 4: Bump Version and Run the Full Suite

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/tmuxcue/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Change the CLI expectation**

Replace the expected output in `tests/test_cli.py` with:

```python
self.assertIn("TmuxCue 0.4.5", output.getvalue())
```

- [ ] **Step 2: Confirm the CLI test fails against `0.4.4`**

Run:

```bash
PYTHONPATH=src:tests "$HOME/anaconda3/bin/python3" \
  -m unittest test_cli.CliTests.test_version_and_help -v
```

Expected: `FAIL` because the CLI still prints `TmuxCue 0.4.4`.

- [ ] **Step 3: Set both authoritative version values**

Set:

```python
__version__ = "0.4.5"
```

in `src/tmuxcue/__init__.py`, and:

```toml
version = "0.4.5"
```

in `pyproject.toml`.

- [ ] **Step 4: Run all tests**

Run:

```bash
PYTHONPATH=src "$HOME/anaconda3/bin/python3" \
  -m unittest discover -s tests -q
bash -n scripts/install.sh scripts/uninstall.sh scripts/update.sh
git diff --check
```

Expected: every test passes and all checks exit `0`.

### Task 5: Install Without Disturbing Existing Conversations

**Files:**
- Install from repository root.

- [ ] **Step 1: Save tmux server, pane, and AI process snapshots**

Run:

```bash
SAFETY=/tmp/tmuxcue-v0.4.5-safety
mkdir -p "$SAFETY"
(pgrep -o -u "$(id -u)" tmux || true) > "$SAFETY/tmux.before"
tmux list-panes -a \
  -F '#{session_name}:#{window_index}.#{pane_index}:#{pane_pid}' \
  | sort > "$SAFETY/panes.before"
ps -eo uid=,pid=,comm= \
  | awk -v uid="$(id -u)" \
      '$1 == uid && ($3 == "codex" || $3 == "claude" || $3 == "claude-code") { print $2 ":" $3 }' \
  | sort > "$SAFETY/ai.before"
```

- [ ] **Step 2: Run the tested installer**

Run:

```bash
./install.sh
```

- [ ] **Step 3: Recreate snapshots and require exact equality**

Run:

```bash
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

- [ ] **Step 4: Wait for a fresh service scan**

Run:

```bash
sleep 5
```

- [ ] **Step 5: Verify Mage Flow metadata and state**

Run:

```bash
PYTHONPATH="$HOME/.local/share/tmuxcue/current/src" \
  "$HOME/.local/share/tmuxcue/current/python" - <<'PY'
import time

from tmuxcue.service import IndicatorDaemon

now = time.time()
daemon = IndicatorDaemon()
daemon.scan()
daemon.refresh_metadata_targets(set(daemon.rows), now)
events = daemon.event_reader.poll(now)
targets = [
    target for target in daemon.rows
    if "mage-flow" in target.lower()
]
assert len(targets) == 1, targets
target = targets[0]
row = daemon.rows[target]
metadata = daemon.metadata[target]
open_ids = daemon.metadata_resolver.codex_open_session_ids(row.pid)
raw = daemon.raw_state(target, row, metadata, now)
event = events.get((row.agent, metadata.session_id))
resolved = daemon.resolve_state(
    row, raw, daemon.prompts.get(target), event, now
)
runtime = daemon.runtime_states.get(target)
print(
    f"session={metadata.session_id} runtime={runtime} "
    f"raw={raw} resolved={resolved} topic={metadata.topic!r}"
)
assert metadata.session_id in open_ids
assert runtime not in {"idle", "aborted"}
assert raw == "working"
assert resolved == "running"
assert metadata.topic and metadata.topic != "c"
PY
```

Expected:

```text
session=<open user rollout UUID>
runtime=<working or unavailable, but not stale idle/aborted>
raw=working
resolved=running
```

- [ ] **Step 6: Verify installed version, active service, stable service PID,
and no crash signatures in the current systemd invocation**

Run:

```bash
"$HOME/.local/bin/tmuxcue" version
systemctl --user is-active tmuxcue.service
PID_BEFORE="$(systemctl --user show tmuxcue.service -p MainPID --value)"
RESTARTS_BEFORE="$(systemctl --user show tmuxcue.service -p NRestarts --value)"
sleep 3
test "$PID_BEFORE" = \
  "$(systemctl --user show tmuxcue.service -p MainPID --value)"
test "$RESTARTS_BEFORE" = \
  "$(systemctl --user show tmuxcue.service -p NRestarts --value)"
```

## Self-Review

- Exact process evidence outranks heuristics.
- Explicit command-line UUID remains highest priority.
- Subagent rows are excluded by the user-thread query.
- Paths outside Codex home are ignored.
- Runtime completion after process start remains authoritative.
- No step controls a tmux pane or AI process.
