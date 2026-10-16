# Codex Goal Runtime State v0.5.3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render durable Codex Goal states correctly so an active Goal remains running between continuation turns.

**Architecture:** Read the exact thread row from Codex's read-only Goal SQLite database, expose Goal-specific runtime states, and allow only those states to promote an idle-looking Codex pane. Keep ordinary rollout-state and background-terminal behavior unchanged.

**Tech Stack:** Python 3.10+ standard library, sqlite3, unittest, tmux CLI, Bash, GitHub REST API.

**Status:** Implemented and locally deployed on 2026-10-16.

## Global Constraints

- Release version is exactly `0.5.3`.
- Runtime dependencies remain empty.
- Python support remains `>=3.10`.
- Goal lookup uses the exact bound thread/session ID.
- Goal databases are opened with SQLite `mode=ro`.
- Goal objective, budget, usage, and timing fields are never queried.
- Background terminal count is not Goal evidence.
- No command may send input or signals to an existing AI or tmux process.
- Existing no-Goal Codex, Claude, Gemini, and direct-terminal behavior must
  remain unchanged.

---

### Task 1: Add Goal database metadata tests

**Files:**
- Modify: `tests/test_metadata.py`

**Interfaces:**
- Consumes: new `MetadataResolver.codex_goal_snapshot(session_id)`.
- Produces: Goal state mapping and exact-thread safety expectations.

- [x] **Step 1: Add a Goal database fixture**

Add:

```python
def create_goal_database(path: Path, rows: list[tuple]) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        create table thread_goals (
            thread_id text primary key,
            status text not null,
            updated_at_ms integer not null
        )
        """
    )
    connection.executemany(
        "insert into thread_goals values (?, ?, ?)",
        rows,
    )
    connection.commit()
    connection.close()
```

- [x] **Step 2: Add exact mapping tests**

Cover:

```python
resolver.codex_goal_snapshot(active_id) == ("goal_active", 123.0)
resolver.codex_goal_snapshot(blocked_id) == ("goal_blocked", 124.0)
resolver.codex_goal_snapshot(paused_id) == ("idle", 125.0)
```

Also cover `complete`, `usage_limited`, and `budget_limited` as idle.

- [x] **Step 3: Add safety and fallback tests**

Assert:

- querying one thread never returns another thread's active Goal;
- unknown statuses return `(None, 0.0)`;
- absent/malformed Goal databases return `(None, 0.0)`;
- `runtime_snapshot()` uses Goal state when a valid row exists;
- no-Goal threads continue through `codex_runtime_snapshot()`.

- [x] **Step 4: Run tests and verify failure**

Run:

```bash
PYTHONPATH=src python3 tests/test_metadata.py
```

Expected: missing `codex_goal_snapshot`.

---

### Task 2: Implement read-only Codex Goal metadata

**Files:**
- Modify: `src/tmuxcue/metadata.py`
- Modify: `tests/test_metadata.py`

**Interfaces:**
- Produces: `codex_goals_database()` and
  `codex_goal_snapshot(session_id) -> RuntimeSnapshot`.
- Extends: `runtime_snapshot(metadata)`.

- [x] **Step 1: Discover the newest Goal database**

Implement:

```python
def codex_goals_database(self) -> Path | None:
    databases = sorted(
        self.codex_home.glob("goals_*.sqlite"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return databases[0] if databases else None
```

Catch disappearing/unreadable files and return no database.

- [x] **Step 2: Query and map the exact thread Goal**

Use:

```sql
select status, updated_at_ms
from thread_goals
where thread_id=?
```

Map:

```python
states = {
    "active": "goal_active",
    "blocked": "goal_blocked",
    "paused": "idle",
    "complete": "idle",
    "usage_limited": "idle",
    "budget_limited": "idle",
}
```

Return milliseconds as seconds and fail closed on any SQLite/type/status error.

- [x] **Step 3: Prefer valid Goal state in runtime resolution**

For Codex:

```python
goal_state, goal_time = self.codex_goal_snapshot(metadata.session_id)
if goal_state is not None:
    return goal_state, goal_time
return self.codex_runtime_snapshot(metadata.session_id)
```

- [x] **Step 4: Run metadata tests**

Run:

```bash
PYTHONPATH=src python3 tests/test_metadata.py
```

Expected: all tests pass.

- [x] **Step 5: Commit**

```bash
git add src/tmuxcue/metadata.py tests/test_metadata.py
git commit -m "feat: read durable Codex Goal state"
```

---

### Task 3: Integrate Goal states into daemon precedence

**Files:**
- Modify: `src/tmuxcue/service.py`
- Modify: `tests/test_daemon.py`

**Interfaces:**
- Consumes: runtime states `goal_active` and `goal_blocked`.
- Preserves: `raw_state()` and `resolve_state()` public signatures.

- [x] **Step 1: Add daemon regression tests**

Cover:

- idle Codex pane + `goal_active` → raw working;
- current Goal observation supersedes an older done hook;
- idle Codex pane + `goal_blocked` → raw blocked / approval;
- idle Codex pane + ordinary runtime working → remains idle;
- idle Codex pane + Goal idle → remains idle;
- blocked pane still returns blocked before runtime polling.

- [x] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=src python3 tests/test_daemon.py
```

Expected: idle Codex panes return before Goal runtime polling.

- [x] **Step 3: Allow scoped Codex runtime polling**

Keep blocked panes as an immediate return. Let idle Codex panes poll runtime,
then apply:

```python
if (
    isinstance(row, PaneRow)
    and row.agent == "codex"
    and row.state != "working"
    and runtime_state not in {"goal_active", "goal_blocked"}
):
    self.raw_observation_times[target] = self.last_scan
    return row.state
```

- [x] **Step 4: Map Goal-specific states**

For `goal_active`:

```python
observation_time = max(
    self.last_scan,
    self.runtime_times.get(target, now),
)
self.raw_observation_times[target] = observation_time
return "working"
```

For `goal_blocked`, record the same fresh observation and return `blocked`.

Terminal rows use `runtime_times[target]` as their current Goal observation.

- [x] **Step 5: Run focused and full regressions**

Run:

```bash
PYTHONPATH=src python3 tests/test_daemon.py
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Expected: all tests pass.

- [x] **Step 6: Commit**

```bash
git add src/tmuxcue/service.py tests/test_daemon.py
git commit -m "feat: supervise active Codex Goals"
```

---

### Task 4: Release v0.5.3 documentation and version

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `pyproject.toml`
- Modify: `src/tmuxcue/__init__.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: public v0.5.3 version and Goal-state documentation.

- [x] **Step 1: Add the failing version assertion**

Require `TmuxCue 0.5.3` in the CLI test.

- [x] **Step 2: Run the CLI test and verify failure**

Run:

```bash
PYTHONPATH=src python3 tests/test_cli.py
```

- [x] **Step 3: Update version and documentation**

Set both version sources to `0.5.3`.

Document:

- turn state, background terminal state, and durable Goal state are distinct;
- active Goal → running;
- blocked Goal → approval;
- paused/complete/limited Goal → done;
- Goal lookup is exact-thread and read-only.

- [x] **Step 4: Run full tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
git diff --check
```

- [x] **Step 5: Commit**

```bash
git add README.md docs/ARCHITECTURE.md pyproject.toml \
  src/tmuxcue/__init__.py tests/test_cli.py
git commit -m "chore: release version 0.5.3"
```

---

### Task 5: Non-disruptive deployment, push, tag, and release

**Files:**
- Modify after verification: `docs/superpowers/specs/2026-10-16-codex-goal-runtime-state-design.md`
- Modify after verification: `docs/superpowers/plans/2026-10-16-codex-goal-runtime-state-v0.5.3.md`

**Interfaces:**
- Consumes: complete v0.5.3 implementation.
- Produces: local deployment, pushed main branch, annotated tag, and latest
  GitHub Release.

- [x] **Step 1: Capture read-only identity snapshots**

Capture the tmux server, pane PID list, and
`tmuxcue __process-snapshot` output.

- [x] **Step 2: Install and compare identities**

Run `./install.sh`, compare all snapshots exactly, and require the user service
to remain active.

- [x] **Step 3: Verify the live affected pane**

Use read-only queries and require:

- the pane remains present;
- its exact Goal database row remains active;
- TmuxCue renders running with a moon icon;
- background terminal text is not used as the evidence;
- no pane or AI process identity changed.

- [x] **Step 4: Record deployment results**

Mark the design implemented and locally deployed. Record only generic counts
and pass/fail results.

- [x] **Step 5: Commit and push main**

```bash
git add docs/superpowers/specs/2026-10-16-codex-goal-runtime-state-design.md \
  docs/superpowers/plans/2026-10-16-codex-goal-runtime-state-v0.5.3.md
git commit -m "docs: record v0.5.3 deployment"
git push origin main
```

- [x] **Step 6: Tag and publish**

```bash
git tag -a v0.5.3 -m "TmuxCue v0.5.3"
git push origin v0.5.3
```

Create a non-draft, non-prerelease latest GitHub Release named
`TmuxCue v0.5.3` with the Goal-state mapping, exact-thread safety, tests, live
verification, and identity-preservation results.

## Execution record

- All 111 unit tests passed.
- TmuxCue v0.5.3 was installed and the user service remained active.
- The tmux server, 60 pane identities, and 12 detected AI process identities
  matched exactly before and after installation.
- The live affected pane's exact durable Goal state remained active.
- TmuxCue displayed the live pane as running with a moon icon.
- Goal state came from the exact read-only Goal database row, not background
  terminal text or rollout prompt content.
- No key, attach command, signal, or process-control action was sent to any
  session.
