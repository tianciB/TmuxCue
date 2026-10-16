# Claude Busy Runtime Precedence v0.5.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Claude Code tmux panes in the running state while their bound local session reports busy, even when a custom backend emits a premature Stop event.

**Architecture:** Allow idle-looking Claude tmux panes to reach the existing runtime resolver, then treat a currently read busy status as live working evidence observed at the current tmux scan. Preserve blocked pane priority and leave other agents and direct terminals unchanged.

**Tech Stack:** Python 3.10+ standard library, unittest, tmux CLI, Bash, GitHub REST API.

**Status:** Implemented and locally deployed on 2026-10-16.

## Global Constraints

- Release version is exactly `0.5.2`.
- Runtime dependencies remain empty.
- Python support remains `>=3.10`.
- Static Claude `✳` titles are not standalone working evidence.
- A blocked pane remains approval even when runtime metadata says busy.
- The scan-time working observation applies only to tmux `PaneRow` values.
- No command may send input or signals to an existing AI or tmux process.
- Existing Codex, native Gemini, and direct-terminal behavior must remain
  unchanged.

---

### Task 1: Add Claude busy-runtime regression tests

**Files:**
- Modify: `tests/test_daemon.py`
- Modify: `tests/test_detector.py`

**Interfaces:**
- Consumes: `IndicatorDaemon.raw_state()` and
  `IndicatorDaemon.resolve_state()`.
- Produces: expected precedence for Claude pane state, runtime status, and hook
  events.

- [x] **Step 1: Add the busy-runtime test**

Add:

```python
def test_claude_busy_runtime_promotes_an_idle_native_pane(self):
    daemon = IndicatorDaemon()
    now = time.time()
    daemon.last_scan = now
    row = PaneRow(
        "%8",
        "g:0",
        "claude",
        "idle",
        "/repo",
        "",
        "/dev/pts/8",
        401,
        int(now - 100),
        "claude",
    )
    metadata = Metadata("claude", "gemini-test", "topic", "session")
    daemon.metadata_resolver.runtime_snapshot = Mock(
        return_value=("working", now - 30)
    )

    raw_state = daemon.raw_state("g:0", row, metadata, now)

    self.assertEqual(raw_state, "working")
    self.assertEqual(daemon.observation_time(row), now)
    self.assertEqual(
        daemon.resolve_state(
            row,
            raw_state,
            None,
            {"state": "done", "event": "Stop", "timestamp": now - 5},
            now,
        ),
        "running",
    )
```

- [x] **Step 2: Add approval, idle, fallback, and static-title tests**

Cover:

- Claude `blocked` pane with runtime working remains approval;
- Claude idle pane with runtime idle returns idle/done;
- Claude idle pane with no runtime snapshot remains idle;
- Codex idle pane does not poll/promote runtime working;
- detector does not classify `✳ Derived session title` as working.

- [x] **Step 3: Run tests and verify failure**

Run:

```bash
PYTHONPATH=src python3 tests/test_daemon.py
PYTHONPATH=src python3 tests/test_detector.py
```

Expected: the Claude idle-pane busy-runtime test fails because `raw_state()`
returns idle before polling runtime metadata.

---

### Task 2: Implement scoped Claude runtime promotion

**Files:**
- Modify: `src/tmuxcue/service.py`
- Modify: `tests/test_daemon.py`
- Modify: `tests/test_detector.py`

**Interfaces:**
- Preserves: `raw_state(target, row, metadata, now) -> str`.
- Changes: idle Claude `PaneRow` values may be promoted to working by runtime
  metadata.

- [x] **Step 1: Refine the pane early-return rule**

Replace the broad non-working pane return with:

```python
if isinstance(row, PaneRow) and row.state == "blocked":
    self.raw_observation_times[target] = self.last_scan
    return row.state
if (
    isinstance(row, PaneRow)
    and row.state != "working"
    and row.agent != "claude"
):
    self.raw_observation_times[target] = self.last_scan
    return row.state
```

- [x] **Step 2: Make a live pane busy read current evidence**

When `runtime_state == "working"` and `row` is a `PaneRow`, set:

```python
observation_time = max(
    observation_time,
    self.last_scan,
    self.runtime_times.get(target, now),
)
```

Return raw `working` through the existing branch.

- [x] **Step 3: Run focused tests**

Run:

```bash
PYTHONPATH=src python3 tests/test_daemon.py
PYTHONPATH=src python3 tests/test_detector.py
```

Expected: all focused tests pass.

- [x] **Step 4: Run agent regressions**

Run:

```bash
PYTHONPATH=src python3 tests/test_metadata.py
PYTHONPATH=src python3 tests/test_events.py
PYTHONPATH=src python3 tests/test_terminal.py
PYTHONPATH=src python3 tests/test_gemini.py
```

Expected: all tests pass.

- [x] **Step 5: Commit**

```bash
git add src/tmuxcue/service.py tests/test_daemon.py tests/test_detector.py
git commit -m "fix: honor live Claude busy state in tmux"
```

---

### Task 3: Release v0.5.2 documentation and version

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `pyproject.toml`
- Modify: `src/tmuxcue/__init__.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: public v0.5.2 version and runtime-precedence documentation.

- [x] **Step 1: Add the failing version assertion**

Require:

```python
self.assertIn("TmuxCue 0.5.2", output.getvalue())
```

- [x] **Step 2: Run the CLI test and verify failure**

Run:

```bash
PYTHONPATH=src python3 tests/test_cli.py
```

Expected: source still reports v0.5.1.

- [x] **Step 3: Update version and documentation**

Set both version sources to `0.5.2`.

Document that a bound Claude `busy` session is authoritative working evidence
for tmux panes when custom model backends emit intermediate Stop events, while
blocked dialogs remain approval.

- [x] **Step 4: Run full tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
git diff --check
```

Expected: all tests pass and no whitespace errors are reported.

- [x] **Step 5: Commit**

```bash
git add README.md docs/ARCHITECTURE.md pyproject.toml \
  src/tmuxcue/__init__.py tests/test_cli.py
git commit -m "chore: release version 0.5.2"
```

---

### Task 4: Non-disruptive deployment, push, tag, and release

**Files:**
- Modify after verification: `docs/superpowers/specs/2026-10-16-claude-busy-runtime-precedence-design.md`
- Modify after verification: `docs/superpowers/plans/2026-10-16-claude-busy-runtime-precedence-v0.5.2.md`

**Interfaces:**
- Consumes: complete v0.5.2 fix.
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
- its bound Claude session reports busy;
- TmuxCue renders `running` with a moon icon;
- no pane or AI process identity changed.

- [x] **Step 4: Record deployment results**

Mark the design implemented and locally deployed. Record only generic counts
and pass/fail results, never session content or process IDs.

- [x] **Step 5: Commit and push main**

```bash
git add docs/superpowers/specs/2026-10-16-claude-busy-runtime-precedence-design.md \
  docs/superpowers/plans/2026-10-16-claude-busy-runtime-precedence-v0.5.2.md
git commit -m "docs: record v0.5.2 deployment"
git push origin main
```

- [x] **Step 6: Tag and publish**

```bash
git tag -a v0.5.2 -m "TmuxCue v0.5.2"
git push origin v0.5.2
```

Create a non-draft, non-prerelease latest GitHub Release named
`TmuxCue v0.5.2` with the root cause, scoped precedence fix, tests, live
verification, and identity-preservation results.

## Execution record

- All 104 unit tests passed.
- TmuxCue v0.5.2 was installed and the user service remained active.
- The tmux server, 55 pane identities, and 10 detected AI process identities
  matched exactly before and after installation.
- The incident was captured while the bound Claude session reported busy and
  TmuxCue displayed done.
- Regression tests confirm that a current busy read now wins over an older
  Stop event while blocked panes still show approval.
- The live session was idle at deployment verification and correctly displayed
  done.
- No key, attach command, signal, or process-control action was sent to any
  session.
