# Gemini Action Title Disambiguation v0.5.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop Gemini interactive-shell inactivity titles from producing false approval indicators while preserving real tool-confirmation detection.

**Architecture:** Make Gemini's native action-required title ambiguous at the detector boundary. Require a bounded current confirmation dialog or a bound permission hook for approval; otherwise classify that title as live working evidence and reuse the existing daemon timestamp precedence.

**Tech Stack:** Python 3.10+ standard library, unittest, tmux CLI, Bash, GitHub REST API.

**Status:** Implemented and locally deployed on 2026-10-15.

## Global Constraints

- Release version is exactly `0.5.1`.
- Runtime dependencies remain empty.
- Python support remains `>=3.10`.
- Gemini uses the same shared approval, running, and done presentation as
  Codex and Claude.
- No command may send input or signals to an existing AI or tmux process.
- Existing Codex and Claude behavior must remain unchanged.
- Deployment must compare tmux, pane, and detected AI process identities before
  and after installation.

---

### Task 1: Add the false-approval regression tests

**Files:**
- Modify: `tests/test_detector.py`
- Modify: `tests/test_daemon.py`

**Interfaces:**
- Consumes: `TmuxDetector.detect_state(agent, title, screen)`.
- Produces: explicit expected behavior for ambiguous Gemini action titles.

- [x] **Step 1: Add detector regression tests**

Add:

```python
def test_gemini_action_title_without_dialog_is_working(self):
    self.assertEqual(
        self.detector.detect_state(
            "gemini",
            "✋  Action Required (repo)",
            "tool output continues",
        ),
        "working",
    )

def test_gemini_action_title_with_confirmation_dialog_is_blocked(self):
    screen = "Action Required\\nAllow once\\nAllow for this session"
    self.assertEqual(
        self.detector.detect_state(
            "gemini",
            "✋  Action Required (repo)",
            screen,
        ),
        "blocked",
    )

def test_old_gemini_confirmation_text_outside_tail_does_not_block(self):
    screen = "Action Required\\nAllow once\\n" + "\\n".join(
        f"output {index}" for index in range(13)
    )
    self.assertEqual(
        self.detector.detect_state(
            "gemini",
            "✋  Action Required (repo)",
            screen,
        ),
        "working",
    )
```

Add a daemon regression showing a newer raw `working` observation returns
`running` over an older Gemini approval hook.

- [x] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=src python3 tests/test_detector.py
PYTHONPATH=src python3 tests/test_daemon.py
```

Expected: the action-title-without-dialog tests fail because the detector
currently returns `blocked`.

- [x] **Step 3: Commit the failing tests with the implementation in Task 2**

No standalone commit; Task 2 completes the test cycle.

---

### Task 2: Disambiguate Gemini action titles

**Files:**
- Modify: `src/tmuxcue/detector.py`
- Modify: `tests/test_detector.py`
- Modify: `tests/test_daemon.py`

**Interfaces:**
- Preserves: raw states `blocked`, `working`, and `idle`.
- Changes: Gemini action titles require bounded screen confirmation before
  returning `blocked`.

- [x] **Step 1: Implement Gemini-specific precedence**

Refactor `detect_state()` so Gemini follows:

```python
approval_tail = "\n".join(screen_lines[-12:])
has_approval_dialog = self.matches_any(approval_patterns, approval_tail)

if agent == "gemini":
    if has_approval_dialog:
        return "blocked"
    if stripped_title.startswith(("✦", "✋")):
        return "working"
else:
    if "action required" in lowered_title:
        return "blocked"
    if self.title_is_working(title_text):
        return "working"
    if has_approval_dialog:
        return "blocked"
```

Then run the existing bounded working-tail check and return idle when no live
evidence remains.

Add `r"allow once"` and `r"allow for this session"` to
`GEMINI_APPROVAL_PATTERNS`.

- [x] **Step 2: Run focused tests**

Run:

```bash
PYTHONPATH=src python3 tests/test_detector.py
PYTHONPATH=src python3 tests/test_daemon.py
```

Expected: all focused tests pass.

- [x] **Step 3: Run Codex/Claude regression tests**

Run:

```bash
PYTHONPATH=src python3 tests/test_metadata.py
PYTHONPATH=src python3 tests/test_events.py
PYTHONPATH=src python3 tests/test_terminal.py
```

Expected: all tests pass.

- [x] **Step 4: Commit**

```bash
git add src/tmuxcue/detector.py tests/test_detector.py tests/test_daemon.py
git commit -m "fix: disambiguate Gemini action-required titles"
```

---

### Task 3: Release v0.5.1 documentation and version

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `pyproject.toml`
- Modify: `src/tmuxcue/__init__.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: user-facing v0.5.1 version and behavior documentation.

- [x] **Step 1: Add the failing version assertion**

Change the CLI test to require:

```python
self.assertIn("TmuxCue 0.5.1", output.getvalue())
```

- [x] **Step 2: Run the CLI test and verify failure**

Run:

```bash
PYTHONPATH=src python3 tests/test_cli.py
```

Expected: installed source still reports v0.5.0.

- [x] **Step 3: Update version and documentation**

Set both version sources to `0.5.1`.

Document that Gemini's native action-required title may be an interactive-shell
focus hint and TmuxCue requires a current confirmation dialog or permission
hook before showing `❓`.

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
git commit -m "chore: release version 0.5.1"
```

---

### Task 4: Non-disruptive deployment, push, tag, and release

**Files:**
- Modify after verification: `docs/superpowers/specs/2026-10-15-gemini-action-title-disambiguation-design.md`
- Modify after verification: `docs/superpowers/plans/2026-10-15-gemini-action-title-disambiguation-v0.5.1.md`

**Interfaces:**
- Consumes: complete v0.5.1 fix.
- Produces: local deployment, pushed main branch, annotated tag, and latest
  GitHub Release.

- [x] **Step 1: Capture read-only identity snapshots**

Capture:

```bash
pgrep -o -u "$(id -u)" tmux
tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}:#{pane_pid}'
PYTHONPATH=src python3 -m tmuxcue __process-snapshot
```

- [x] **Step 2: Install and compare identities**

Run:

```bash
./install.sh
```

Compare all three snapshots exactly and require the service to be active.

- [x] **Step 3: Verify the live Gemini correction**

Use read-only tmux queries and require:

- the Gemini pane remains present;
- no process identity changed;
- an action-required title with no current confirmation dialog produces a
  TmuxCue running state and moon icon rather than approval/`❓`.

- [x] **Step 4: Record deployment results**

Mark the design implemented and locally deployed. Record only generic counts
and pass/fail results, never session content or process IDs.

- [x] **Step 5: Commit and push main**

```bash
git add docs/superpowers/specs/2026-10-15-gemini-action-title-disambiguation-design.md \
  docs/superpowers/plans/2026-10-15-gemini-action-title-disambiguation-v0.5.1.md
git commit -m "docs: record v0.5.1 deployment"
git push origin main
```

- [x] **Step 6: Tag and publish**

```bash
git tag -a v0.5.1 -m "TmuxCue v0.5.1"
git push origin v0.5.1
```

Create a non-draft, non-prerelease GitHub Release named `TmuxCue v0.5.1`,
mark it latest, and include the root cause, detector fix, regression tests, and
identity-preservation verification.

## Execution record

- All 99 unit tests passed.
- TmuxCue v0.5.1 was installed and the user service remained active.
- The tmux server, 54 pane identities, and 12 detected AI process identities
  matched exactly before and after installation.
- The live Gemini pane kept its native action-required title while TmuxCue
  correctly rendered `running` with a moon icon.
- No key, attach command, signal, or process-control action was sent to the
  Gemini pane or any other process.
