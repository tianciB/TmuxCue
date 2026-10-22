# Codex Live Working Goal Precedence v0.5.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent an older blocked Codex Goal from overriding a currently working tmux pane.

**Architecture:** Keep side conversations and visible approval dialogs authoritative, then let a current Codex working pane override only `goal_blocked`. Preserve the existing runtime path for ordinary completion and abort events so stale native spinners can still be cleared.

**Tech Stack:** Python 3.10+ standard library, unittest, tmux CLI, Bash, GitHub REST API.

**Status:** Implemented, deployed, and released as v0.5.5.

## Global Constraints

- Release version is exactly `0.5.5`.
- Runtime dependencies remain empty.
- Python support remains `>=3.10`.
- Current side-conversation precedence remains unchanged.
- A visible approval dialog remains authoritative.
- Idle exact Goal supervision remains unchanged.
- Direct-terminal, Claude, and Gemini behavior remain unchanged.
- No command may send input or signals to an existing AI or tmux process.

---

### Task 1: Add the stale blocked Goal regression

**Files:**
- Modify: `tests/test_daemon.py`

**Interfaces:**
- Exercises: `IndicatorDaemon.raw_state(...)`.
- Preserves: existing public daemon method signatures.

- [x] **Step 1: Add the failing regression**

Add a test with:

```python
daemon = IndicatorDaemon()
now = time.time()
daemon.last_scan = now
row = PaneRow(
    "%1",
    "s:0",
    "codex",
    "working",
    "/repo",
    "",
    "/dev/pts/1",
)
metadata = Metadata("codex", "model", "topic", "session")
daemon.metadata_resolver.runtime_snapshot = Mock(
    return_value=("goal_blocked", now - 3600)
)

raw_state = daemon.raw_state("s:0", row, metadata, now)

self.assertEqual(raw_state, "working")
self.assertEqual(daemon.observation_time(row), now)
self.assertEqual(
    daemon.resolve_state(row, raw_state, None, None, now),
    "running",
)
```

- [x] **Step 2: Run the focused test and verify failure**

Run:

```bash
PYTHONPATH=src python3 tests/test_daemon.py
```

Expected: the new test receives `blocked` instead of `working`.

---

### Task 2: Prioritize the current working pane over `goal_blocked`

**Files:**
- Modify: `src/tmuxcue/service.py`
- Modify: `tests/test_daemon.py`

**Interfaces:**
- Consumes: `PaneRow.state`.
- Preserves: `IndicatorDaemon.raw_state(...) -> str`.

- [x] **Step 1: Add the minimal precedence branch**

Before the generic Goal mapping in `raw_state()`, add:

```python
if (
    runtime_state == "goal_blocked"
    and isinstance(row, PaneRow)
    and row.agent == "codex"
    and row.state == "working"
):
    self.raw_observation_times[target] = self.last_scan
    return "working"
```

The existing early blocked-pane branch and side-active branch remain ahead of
this code.

- [x] **Step 2: Run focused daemon tests**

Run:

```bash
PYTHONPATH=src python3 tests/test_daemon.py
```

Expected: all daemon tests pass, including the existing idle blocked Goal,
visible approval, side-active, completion, and abort regressions.

- [x] **Step 3: Run full tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
git diff --check
```

Expected: all tests pass.

- [x] **Step 4: Commit**

```bash
git add src/tmuxcue/service.py tests/test_daemon.py
git commit -m "fix: prioritize live Codex work over stale Goal block"
```

---

### Task 3: Release v0.5.5 documentation and version

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `pyproject.toml`
- Modify: `src/tmuxcue/__init__.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: public v0.5.5 version and precedence documentation.

- [x] **Step 1: Require v0.5.5 in the CLI test**

Change the expected version to:

```python
self.assertIn("TmuxCue 0.5.5", output.getvalue())
```

- [x] **Step 2: Run the CLI test and verify failure**

```bash
PYTHONPATH=src python3 tests/test_cli.py
```

Expected: installed source still reports v0.5.4.

- [x] **Step 3: Update version and documentation**

Set both version sources to `0.5.5`.

Document:

- visible working and approval UI is newer than a stale blocked Goal;
- exact Goal state still supplements an idle pane;
- side-conversation precedence remains unchanged;
- completion and abort runtime evidence can still clear stale spinners.

- [x] **Step 4: Run full and privacy tests**

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 tests/test_privacy.py
git diff --check
```

- [x] **Step 5: Commit**

```bash
git add README.md docs/ARCHITECTURE.md pyproject.toml \
  src/tmuxcue/__init__.py tests/test_cli.py
git commit -m "chore: release version 0.5.5"
```

---

### Task 4: Non-disruptive deployment and live verification

**Files:**
- Modify after verification: `docs/superpowers/specs/2026-10-22-codex-live-working-goal-precedence-design.md`
- Modify after verification: `docs/superpowers/plans/2026-10-22-codex-live-working-goal-precedence-v0.5.5.md`

**Interfaces:**
- Consumes: complete v0.5.5 implementation.
- Produces: local deployment and a verified live `self-study` state.

- [x] **Step 1: Capture identity snapshots**

Capture:

- tmux server PID;
- sorted pane identity list;
- `tmuxcue __process-snapshot`;
- current `tmuxcue.service` PID.

- [x] **Step 2: Install and compare**

Run:

```bash
./install.sh
```

Require exact tmux, pane, and detected AI identity matches. Only the service
PID may change, and the service must remain active.

- [x] **Step 3: Verify the installed regression**

Run a synthetic installed-package assertion for a working pane plus
`goal_blocked`.

- [x] **Step 4: Verify `self-study`**

Without sending input, require:

```text
pane_state=working
runtime_state=goal_blocked
managed_state=running
```

If the live pane naturally changes before verification, record the new native
and Goal state instead and verify the corresponding documented precedence.

- [x] **Step 5: Record and commit deployment**

Mark the design and plan implemented and record generic test and identity
counts:

```bash
git commit -m "docs: record v0.5.5 deployment"
```

---

### Task 5: Push, tag, and publish v0.5.5

**Files:**
- No source changes expected.

**Interfaces:**
- Produces: synchronized `main`, annotated tag `v0.5.5`, and latest GitHub
  Release `TmuxCue v0.5.5`.

- [x] **Step 1: Verify publication preconditions**

Require:

- clean worktree;
- `origin/main` is an ancestor of local `main`;
- no local or remote `v0.5.5` tag.

- [x] **Step 2: Push `main`**

```bash
git push origin main
```

- [x] **Step 3: Create and push the annotated tag**

```bash
git tag -a v0.5.5 -m "TmuxCue v0.5.5"
git push origin v0.5.5
```

- [x] **Step 4: Publish and verify the GitHub Release**

Create a non-draft, non-prerelease release named `TmuxCue v0.5.5`, mark it
latest, and verify both the tag-specific and latest release endpoints.

## Execution record

- All 123 unit and privacy tests passed.
- The installed package reports `TmuxCue 0.5.5`.
- The tmux server, all 58 pane identities, and all 11 detected AI process
  identities were unchanged across installation.
- Only `tmuxcue.service` restarted, and it remained active.
- The installed live-working precedence regression passed.
- Live `self-study` reported `pane_state=working`,
  `runtime_state=goal_blocked`, and `managed_state=running`.
- `main`, annotated tag `v0.5.5`, and the latest GitHub Release were
  published.
