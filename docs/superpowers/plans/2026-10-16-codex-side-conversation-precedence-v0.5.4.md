# Codex Side Conversation Precedence v0.5.4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the currently displayed Codex side conversation authoritative over the parent thread's Goal, hooks, and prompt state.

**Architecture:** Detect the Codex TUI's bounded side-context label, store a boolean on the pane row, and short-circuit parent runtime/hook/prompt resolution while that side is displayed. Include the flag in row identity so returning to the parent restores normal state immediately.

**Tech Stack:** Python 3.10+ standard library, unittest, tmux CLI, Bash, GitHub REST API.

**Status:** Implemented, deployed, and released as v0.5.4.

## Global Constraints

- Release version is exactly `0.5.4`.
- Runtime dependencies remain empty.
- Python support remains `>=3.10`.
- Side state is detected only from the current bounded pane UI.
- `thread_spawn_edges` is not used for side-conversation detection.
- Side text, questions, history, and thread IDs are not persisted.
- No command may send input or signals to an existing AI or tmux process.
- Existing parent Goal, no-side Codex, Claude, Gemini, and direct-terminal
  behavior must remain unchanged.

---

### Task 1: Add side-context detector tests

**Files:**
- Modify: `tests/test_detector.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Produces: `TmuxDetector.side_conversation_active(screen) -> bool`.
- Extends: `PaneRow.side_active`.

- [x] **Step 1: Add PaneRow default test**

Construct an existing positional `PaneRow` and assert:

```python
self.assertFalse(row.side_active)
```

- [x] **Step 2: Add bounded side-label tests**

Require:

```python
self.assertTrue(
    self.detector.side_conversation_active(
        "output\nSide from main thread · ctrl+c to close"
    )
)
self.assertTrue(
    self.detector.side_conversation_active(
        "Side from parent thread (task) · ctrl+c to close"
    )
)
self.assertFalse(
    self.detector.side_conversation_active(
        "ctrl+x for side"
    )
)
```

Place an active-side label more than 20 lines above the bottom and assert false.

- [x] **Step 3: Add scan propagation test**

Mock `capture-pane` with an active side label and assert the resulting
`PaneRow.side_active` is true.

- [x] **Step 4: Run tests and verify failure**

Run:

```bash
PYTHONPATH=src python3 tests/test_core.py
PYTHONPATH=src python3 tests/test_detector.py
```

Expected: missing `side_active` and side detector.

---

### Task 2: Implement side-context pane detection

**Files:**
- Modify: `src/tmuxcue/core.py`
- Modify: `src/tmuxcue/detector.py`
- Modify: `tests/test_core.py`
- Modify: `tests/test_detector.py`

**Interfaces:**
- Adds: `PaneRow.side_active: bool = False`.
- Adds: `TmuxDetector.side_conversation_active(screen)`.

- [x] **Step 1: Add the pane field**

Append:

```python
side_active: bool = False
```

to `PaneRow` so every existing positional constructor remains valid.

- [x] **Step 2: Implement bounded detection**

Use only the final 20 lines:

```python
SIDE_CONTEXT = re.compile(
    r"^\s*Side from (?:main thread|parent thread(?:\s*\([^)]*\))?)"
    r"(?:\s|·|$)",
    re.I,
)
```

Return true when any bounded line matches.

- [x] **Step 3: Propagate the flag from scan**

Pass:

```python
side_active=self.side_conversation_active(screen)
```

when constructing `PaneRow`.

- [x] **Step 4: Run focused tests**

Run:

```bash
PYTHONPATH=src python3 tests/test_core.py
PYTHONPATH=src python3 tests/test_detector.py
```

Expected: all tests pass.

- [x] **Step 5: Commit**

```bash
git add src/tmuxcue/core.py src/tmuxcue/detector.py \
  tests/test_core.py tests/test_detector.py
git commit -m "feat: detect active Codex side conversations"
```

---

### Task 3: Make side pane state authoritative

**Files:**
- Modify: `src/tmuxcue/service.py`
- Modify: `tests/test_daemon.py`

**Interfaces:**
- Consumes: `PaneRow.side_active`.
- Preserves: public daemon method signatures.

- [x] **Step 1: Add state-precedence tests**

Cover:

- side-active idle pane + parent `goal_active` → idle/done;
- side-active working pane + parent done hook → running;
- side-active blocked pane → approval;
- parent prompt state is ignored while side is active;
- side false + parent `goal_active` → running;
- row identity differs when only `side_active` changes.

- [x] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=src python3 tests/test_daemon.py
```

Expected: parent Goal/hook/prompt still overrides side state.

- [x] **Step 3: Include side state in identity**

Append `row.side_active` to `row_identity()` and `metadata_source_key()` for
tmux panes.

- [x] **Step 4: Bypass parent runtime**

At the start of `raw_state()`:

```python
if isinstance(row, PaneRow) and row.agent == "codex" and row.side_active:
    self.raw_observation_times[target] = self.last_scan
    return row.state
```

- [x] **Step 5: Bypass parent prompt and hook**

In `apply_targets()`:

```python
side_active = (
    isinstance(row, PaneRow)
    and row.agent == "codex"
    and row.side_active
)
prompt = None if side_active else self.prompts.get(target)
hook_event = None if side_active else existing_hook_lookup
```

- [x] **Step 6: Run focused and full tests**

Run:

```bash
PYTHONPATH=src python3 tests/test_daemon.py
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Expected: all tests pass.

- [x] **Step 7: Commit**

```bash
git add src/tmuxcue/service.py tests/test_daemon.py
git commit -m "fix: prioritize active Codex side state"
```

---

### Task 4: Release v0.5.4 documentation and version

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `pyproject.toml`
- Modify: `src/tmuxcue/__init__.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: public v0.5.4 version and side-precedence documentation.

- [x] **Step 1: Require v0.5.4 in the CLI test**

- [x] **Step 2: Run the CLI test and verify failure**

```bash
PYTHONPATH=src python3 tests/test_cli.py
```

- [x] **Step 3: Update version and documentation**

Set both version sources to `0.5.4`.

Document:

- active displayed side UI is highest priority;
- parent Goal/hooks/prompts are ignored while side is selected;
- switching back restores parent Goal;
- side detection is bounded and does not use subagent edges.

- [x] **Step 4: Run full tests**

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
git diff --check
```

- [x] **Step 5: Commit**

```bash
git add README.md docs/ARCHITECTURE.md pyproject.toml \
  src/tmuxcue/__init__.py tests/test_cli.py
git commit -m "chore: release version 0.5.4"
```

---

### Task 5: Non-disruptive deployment, push, tag, and release

**Files:**
- Modify after verification: `docs/superpowers/specs/2026-10-16-codex-side-conversation-precedence-design.md`
- Modify after verification: `docs/superpowers/plans/2026-10-16-codex-side-conversation-precedence-v0.5.4.md`

**Interfaces:**
- Consumes: complete v0.5.4 implementation.
- Produces: local deployment, pushed main branch, annotated tag, and latest
  GitHub Release.

- [x] **Step 1: Capture identity snapshots**

Capture the tmux server, pane PID list, and
`tmuxcue __process-snapshot` output.

- [x] **Step 2: Install and compare**

Run `./install.sh`, compare snapshots exactly, and require the service active.

- [x] **Step 3: Verify**

If a side conversation is currently displayed, verify its state is
authoritative. Otherwise verify synthetic regressions plus unchanged live
parent Goal behavior.

- [x] **Step 4: Record and commit deployment**

Mark the design and plan implemented, then commit with:

```bash
git commit -m "docs: record v0.5.4 deployment"
```

- [x] **Step 5: Push, tag, and publish**

Push `main`, create and push annotated `v0.5.4`, then create a non-draft,
non-prerelease latest GitHub Release named `TmuxCue v0.5.4`.

## Execution record

- All 122 unit and privacy tests passed.
- The installed package reports `TmuxCue 0.5.4`.
- The tmux server, all 62 pane identities, and all 11 detected AI process
  identities were unchanged across installation.
- Only `tmuxcue.service` restarted, and it remained active.
- The installed side-precedence regression passed.
- No side conversation was displayed during live verification; the exact
  parent Goal remained `goal_active` and the window remained running.
- `main`, annotated tag `v0.5.4`, and the latest GitHub Release were
  published.
