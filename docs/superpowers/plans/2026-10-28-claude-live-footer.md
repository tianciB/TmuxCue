# Claude Live Footer Activity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Claude tmux windows running when their current footer shows progress or an explicit wait for background agents.

**Architecture:** Add a Claude-only, bounded composer/footer recognizer to the pane detector. Feed its existing `working` result through the unchanged daemon freshness rules. Do not change runtime metadata, hooks, completion grace, or other agents.

**Tech Stack:** Python 3.10+ standard library, unittest, tmux CLI, systemd user service, Bash, Git.

**Execution:** Inline under the user's instruction to fix and push. The optional executing-plans skill is not installed; follow the explicit red/green, review, deployment, and delivery checkpoints here.

## Global Constraints

- Inspect only the final 20 captured pane lines.
- Keep visible approvals authoritative.
- Ignore historical, quoted, completed, interrupted, and malformed status text.
- Do not treat a static `✳` title as activity.
- Preserve Codex, native Gemini, direct-terminal, metadata binding, and lifecycle hook behavior.
- Preserve Python `>=3.10` and the empty runtime dependency list.
- Do not send input or signals to tmux or AI processes.
- Do not change global tmux options, hooks, application settings, or models.
- Do not create a tag or GitHub Release.

## File Responsibilities

- `src/tmuxcue/detector.py`: activity grammar, bounded composer detection, Claude-only invocation.
- `tests/test_claude_activity.py`: synthetic detector and daemon integration regressions.
- `README.md`: user-facing progress/background-agent behavior.
- `docs/ARCHITECTURE.md`: UI anchoring and unchanged state precedence.
- This plan and its approved design: verification and delivery record.

---

### Task 1: Reproduce and implement Claude footer recognition

**Files:**
- Create: `tests/test_claude_activity.py`
- Modify: `src/tmuxcue/detector.py`

**Interfaces:**
- Add: `TmuxDetector.claude_footer_is_working(screen: str) -> bool`.
- Preserve: `TmuxDetector.detect_state(agent, title, screen) -> str`.
- Exercise: `IndicatorDaemon.raw_state` and `resolve_state`, with runtime metadata mocked and no live tmux writes.

- [ ] **Step 1: Add these synthetic regressions**

Create `tests/test_claude_activity.py` with:

```python
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from tmuxcue.core import Metadata, PaneRow
from tmuxcue.detector import TmuxDetector
from tmuxcue.service import IndicatorDaemon


RULE = "─" * 40
PROGRESS = "✽ Thinking… (1m 58s · ↓ 1.5k tokens)"
WAITING = "✻ Waiting for 1 background agent to finish"
COMPLETED = "✻ Baked for 2m 10s · done 9:00 AM"


def frame(status, *, history="", prompt="❯ ", footer="  Model · high\n  auto mode on"):
    return f"{history}\n{status}\n\n{RULE}\n{prompt}\n{RULE}\n{footer}\n"


class ClaudeActivityDetectorTests(unittest.TestCase):
    def setUp(self):
        self.detector = TmuxDetector(command=lambda _args, _timeout=5: "")

    def state(self, screen, agent="claude"):
        return self.detector.detect_state(agent, "✳ Session title", screen)

    def test_progress_without_interrupt_hint_is_working(self):
        for glyph in "·✢✳✶✻✽":
            for meter in (
                "8s · ↓ 42 tokens",
                "1m 58s · ↑ 1.5k tokens",
                "2h 1m 8s · ↓ 1,024 tokens · thinking",
            ):
                with self.subTest(glyph=glyph, meter=meter):
                    self.assertEqual(
                        self.state(frame(f"{glyph} Thinking… ({meter})")),
                        "working",
                    )

    def test_ascii_ellipsis_is_working(self):
        self.assertEqual(
            self.state(frame("✳ Thinking... (8s · ↑ 12 tokens)")), "working",
        )

    def test_positive_background_agent_wait_is_working(self):
        for count, noun in ((1, "agent"), (2, "agents"), (12, "agents")):
            with self.subTest(count=count):
                self.assertEqual(
                    self.state(frame(f"✻ Waiting for {count} background {noun} to finish")),
                    "working",
                )

    def test_input_whitespace_and_multiline_composer_are_supported(self):
        self.assertEqual(
            self.state(frame("  " + PROGRESS, prompt="  ❯\u00a0queued input\n  second line")),
            "working",
        )

    def test_completed_and_non_activity_statuses_remain_idle(self):
        for status in (
            COMPLETED,
            "✻ Interrupted",
            "✻ Waiting for 0 background agents to finish",
            "✻ Waiting for -1 background agents to finish",
            "✻ Waiting for 1 background terminal to finish",
            "✻ Waiting for background agents",
            "Thinking… (8s · ↓ 42 tokens)",
            "✻ Thinking (8s · ↓ 42 tokens)",
            "✻ Thinking… (8s)",
            "✻ Thinking… (8s · 42 tokens)",
            "✻ Thinking… (↓ 42 tokens)",
            "Finished.",
            "",
        ):
            with self.subTest(status=status):
                self.assertEqual(self.state(frame(status)), "idle")

    def test_static_native_title_is_not_activity(self):
        self.assertEqual(self.state(""), "idle")
        self.assertEqual(self.state(frame("Ready.")), "idle")

    def test_old_output_does_not_override_current_completion(self):
        for old in (PROGRESS, WAITING):
            with self.subTest(old=old):
                self.assertEqual(
                    self.state(frame(COMPLETED, history=old)), "idle",
                )

    def test_earlier_composer_does_not_override_current_composer(self):
        self.assertEqual(
            self.state(frame(COMPLETED, history=frame(WAITING))), "idle",
        )

    def test_quoted_and_code_fenced_activity_is_not_current_status(self):
        for status in ("> " + WAITING, "`" + PROGRESS + "`", PROGRESS + "\n```"):
            with self.subTest(status=status):
                self.assertEqual(self.state(frame(status)), "idle")

    def test_activity_requires_a_complete_composer(self):
        for screen in (
            PROGRESS,
            WAITING,
            f"{PROGRESS}\n❯ \n{RULE}",
            f"{PROGRESS}\n{RULE}\n❯ ",
            f"{PROGRESS}\n{RULE}\nnot a prompt\n{RULE}",
        ):
            with self.subTest(screen=screen):
                self.assertEqual(self.state(screen), "idle")

    def test_detection_is_bounded_to_the_last_twenty_lines(self):
        screen = frame(WAITING) + "\n".join(f"output {i}" for i in range(20))
        self.assertEqual(self.state(screen), "idle")

    def test_visible_approval_wins_over_progress_and_waiting(self):
        for status in (PROGRESS, WAITING):
            with self.subTest(status=status):
                self.assertEqual(
                    self.state(frame(status, footer="Enter to select · Esc to cancel")),
                    "blocked",
                )

    def test_claude_footer_does_not_change_other_agents(self):
        for agent in ("codex", "gemini", "unknown"):
            for status in (PROGRESS, WAITING):
                with self.subTest(agent=agent, status=status):
                    self.assertEqual(self.state(frame(status), agent), "idle")

    def test_existing_interrupt_hints_still_work_without_a_composer(self):
        for hint in ("Esc to interrupt", "Ctrl+C to interrupt"):
            with self.subTest(hint=hint):
                self.assertEqual(self.state(hint), "working")

    def test_scan_propagates_current_claude_activity(self):
        def command(args, _timeout=5):
            if args[:3] == ("tmux", "list-panes", "-a"):
                return "%1\ts:0.0\tclaude\t/repo\t✳ Session title\t/dev/pts/1"
            if args[:2] == ("tmux", "capture-pane"):
                return frame(WAITING)
            return ""

        detector = TmuxDetector(
            command=command, process_provider=lambda _root, _uid: {},
        )
        self.assertEqual(detector.scan()["s:0"].state, "working")


class ClaudeActivityDaemonTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(os.environ, {"CODEX_HOME": temporary.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.now = 10_000.0
        self.detector = TmuxDetector(command=lambda _args, _timeout=5: "")
        self.daemon = IndicatorDaemon()
        self.daemon.last_scan = self.now
        self.daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("idle", self.now - 60),
        )
        self.metadata = Metadata("claude", "test-model", "topic", "test-session")

    def row(self, status, **kwargs):
        screen = frame(status, **kwargs)
        native = self.detector.detect_state("claude", "✳ Session title", screen)
        return PaneRow(
            "%1", "s:0", "claude", native, "/repo", "", "/dev/pts/1", 123, 1, "claude",
        )

    def resolve(self, row, hook=None, now=None):
        now = self.now if now is None else now
        raw = self.daemon.raw_state(row.window, row, self.metadata, now)
        return self.daemon.resolve_state(row, raw, None, hook, now)

    def test_live_progress_and_waiting_supersede_old_idle_and_stop(self):
        for status in (PROGRESS, WAITING):
            with self.subTest(status=status):
                row = self.row(status)
                self.assertEqual(row.state, "working")
                self.assertEqual(
                    self.resolve(row, {"state": "done", "timestamp": self.now - 5}),
                    "running",
                )
                self.assertEqual(self.daemon.observation_time(row), self.now)

    def test_live_footer_works_when_runtime_is_missing(self):
        self.daemon.metadata_resolver.runtime_snapshot.return_value = (None, 0)
        self.assertEqual(self.resolve(self.row(WAITING)), "running")

    def test_existing_busy_runtime_still_promotes_unrecognized_footer(self):
        self.daemon.metadata_resolver.runtime_snapshot.return_value = (
            "working", self.now - 60,
        )
        self.assertEqual(
            self.resolve(
                self.row("Ready."),
                {"state": "done", "timestamp": self.now - 5},
            ),
            "running",
        )

    def test_visible_approval_stays_authoritative(self):
        self.daemon.metadata_resolver.runtime_snapshot.return_value = (
            "working", self.now - 60,
        )
        row = self.row(PROGRESS, footer="Enter to select · Esc to cancel")
        self.assertEqual(self.resolve(row), "approval")
        self.daemon.metadata_resolver.runtime_snapshot.assert_not_called()

    def test_completed_footer_clears_previous_wait_without_extra_grace(self):
        self.assertEqual(self.resolve(self.row(WAITING)), "running")
        self.daemon.last_scan = self.now + 3
        self.daemon.metadata_resolver.runtime_snapshot.return_value = (
            "idle", self.now + 2,
        )
        self.assertEqual(
            self.resolve(
                self.row(COMPLETED),
                {"state": "done", "timestamp": self.now + 2},
                now=self.now + 3,
            ),
            "done",
        )

    def test_newer_completion_still_supersedes_stale_working_scan(self):
        self.daemon.last_scan = self.now - 5
        self.daemon.metadata_resolver.runtime_snapshot.return_value = (
            "idle", self.now - 1,
        )
        self.assertEqual(self.resolve(self.row(PROGRESS)), "done")

    def test_newer_approval_still_supersedes_stale_working_scan(self):
        self.daemon.last_scan = self.now - 5
        self.assertEqual(
            self.resolve(
                self.row(WAITING),
                {"state": "approval", "timestamp": self.now - 1},
            ),
            "approval",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Confirm the failing regressions**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests \
  python3 -m unittest test_claude_activity -v
```

Expected: current progress/wait tests fail because the old detector returns
idle. Negative cases and existing precedence compatibility cases remain green.

- [ ] **Step 3: Add the minimal detector implementation**

Add these module constants after the existing working-pattern constants:

```python
CLAUDE_ACTIVITY = re.compile(
    r"^[·✢✳✶✻✽]\s+(?:"
    r"Waiting for [1-9]\d* background agents? to finish"
    r"|[^\r\n]+(?:…|\.{3})\s+"
    r"\(\d+[hms](?:\s+\d+[hms])*\s+·\s+"
    r"[↑↓]\s*\d+(?:[.,]\d+)*[kKmM]?\s+tokens"
    r"(?:\s+·\s+[^)\r\n]+)?\))$",
    re.I,
)
CLAUDE_PROMPT = re.compile(r"^\s*❯(?:\s|$)")
CLAUDE_SEPARATOR = re.compile(r"^[─━]{3,}$")
```

Add this static method to `TmuxDetector`, before `detect_state()`:

```python
@staticmethod
def claude_footer_is_working(screen: str) -> bool:
    lines = (screen or "").splitlines()[-20:]
    for index in range(len(lines) - 1, -1, -1):
        if not CLAUDE_PROMPT.match(lines[index]):
            continue
        if index < 2 or not CLAUDE_SEPARATOR.fullmatch(lines[index - 1].strip()):
            return False
        if not any(
            CLAUDE_SEPARATOR.fullmatch(line.strip())
            for line in lines[index + 1 :]
        ):
            return False
        candidate = index - 2
        while candidate >= 0 and not lines[candidate].strip():
            candidate -= 1
        return candidate >= 0 and bool(
            CLAUDE_ACTIVITY.fullmatch(lines[candidate].strip())
        )
    return False
```

Immediately before the existing `working_tail` check, add:

```python
if agent == "claude" and self.claude_footer_is_working(screen):
    return "working"
```

- [ ] **Step 4: Run focused and complete tests and review**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests \
  python3 -m unittest test_claude_activity -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m unittest discover -s tests -q
git diff --check
git diff -- src/tmuxcue/detector.py
```

Expected: focused tests report `OK`, all original 135 tests remain green,
and production changes are confined to the Claude-only detector path.

- [ ] **Step 5: Commit the tested implementation**

```bash
git add src/tmuxcue/detector.py tests/test_claude_activity.py
git commit -m "fix: detect live Claude progress and background-agent waits"
```

### Task 2: Document the state boundary

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:**
- Consumes: the detector behavior from Task 1.
- Produces: consistent user and architecture descriptions without a new setting.

- [ ] **Step 1: Replace the README Claude busy paragraph with this text**

```markdown
Claude Code sessions that use custom API backends may emit an intermediate
`Stop` event before all work has finished. For tmux panes, TmuxCue checks
both the bound Claude session's live `busy` status and the current status
line immediately above the input composer. Progress with an elapsed/token
meter, or an explicit wait for one or more background agents, keeps the
running moon active even when the main turn temporarily reports idle.
A real approval dialog still takes priority and shows `❓`.

Only a current, bounded footer is used: old output, quoted status text,
completion summaries, and the static `✳` title do not imply ongoing work.
A genuinely idle pane returns to `✅` without an added completion delay.
Claude CLI sessions using Gemini-backed models follow these Claude rules;
native Gemini CLI behavior is unchanged.
```

- [ ] **Step 2: Replace the architecture Claude paragraph with this text**

```markdown
For Claude tmux panes, a bound local session whose live status is `busy` is
authoritative working evidence even when the native title is a static derived
session name. Each current read of that busy state can supersede an older
intermediate `Stop` event from a custom model backend. A blocked pane remains
approval. An idle runtime without newer native working evidence remains done.
The static `✳` title prefix is not independently treated as a spinner.

Claude activity detection also locates the last input composer within the
final 20 captured lines, requiring its upper and lower separators. Only the
nearest nonblank status line above the upper separator is considered:
an elapsed/token progress meter or a positive background-agent wait is
working evidence. Earlier output, other composers, and completed summaries
are ignored. This uses the existing capture and adds no transcript/task
inspection or persistent data. Existing scan/runtime/hook timestamps resolve
fresh activity against older idle/Stop evidence and still allow newer
completion or approval to supersede a stale scan.
```

- [ ] **Step 3: Check and commit documentation**

```bash
git diff --check
git add README.md docs/ARCHITECTURE.md
git commit -m "docs: explain Claude live footer activity"
```

### Task 3: Deploy only the detector update

**Files:**
- No tracked installer or configuration changes.
- A new installed release directory and private audit directory outside Git.

**Interfaces:**
- Consumes: tested detector and unchanged installed runtime/interpreter.
- Produces: healthy installed fix, identity/configuration equality checks, and rollback capability.

- [ ] **Step 1: Stage and test the installed runtime**

```bash
set -euo pipefail
umask 077
ROOT="$PWD"
BASE="$HOME/.local/share/tmuxcue"
PREVIOUS="$(readlink "$BASE/current")"
STAGE="$BASE/releases/$(date -u +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD)"
AUDIT="$(mktemp -d "${TMPDIR:-/tmp}/tmuxcue-claude-footer.XXXXXX")"
mkdir -m 700 "$STAGE"
cp -a "$BASE/current/." "$STAGE/"
cp "$ROOT/src/tmuxcue/detector.py" "$STAGE/src/tmuxcue/detector.py"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$STAGE/src" \
  "$STAGE/python" -m unittest discover -s "$ROOT/tests" -q
printf 'STAGE=%s\nAUDIT=%s\n' "$STAGE" "$AUDIT"
```

Compare all non-bytecode staged files with the previous release: only
`src/tmuxcue/detector.py` may differ. The Python interpreter symlink,
existing metadata-isolation fix, hooks, and helpers must match.

- [ ] **Step 2: Capture safety snapshots and activate with rollback**

In the shell holding the deployment variables from Step 1:

```bash
snapshot() {
    destination="$1"
    tmux display-message -p '#{pid}' > "$destination.server"
    tmux list-panes -a \
      -F '#{session_id}:#{window_id}:#{pane_id}:#{pane_pid}:#{pane_tty}:#{pane_dead}' \
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
    config_path(), state_path(), gemini_settings_registry_path(),
    *managed_gemini_settings_paths(),
}
for path in sorted(paths):
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"
    print(f"{path}\t{digest}")
PY
}
SWITCHED=0
finish() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ] && [ "$SWITCHED" -eq 1 ]; then
        ln -s "$PREVIOUS" "$BASE/.footer-rollback-$$"
        mv -Tf "$BASE/.footer-rollback-$$" "$BASE/current"
        systemctl --user restart tmuxcue.service
    fi
    printf 'Private audit: %s\n' "$AUDIT"
    exit "$status"
}
trap finish EXIT
snapshot "$AUDIT/before"
ln -s "$STAGE" "$BASE/.footer-current-$$"
mv -Tf "$BASE/.footer-current-$$" "$BASE/current"
SWITCHED=1
systemctl --user restart tmuxcue.service
sleep 2
systemctl --user is-active --quiet tmuxcue.service
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$BASE/current/src:$ROOT/tests" \
  "$BASE/current/python" -m unittest test_claude_activity -q
snapshot "$AUDIT/after"
for item in server panes agents options config; do
    diff -u "$AUDIT/before.$item" "$AUDIT/after.$item"
done
```

Additionally compare server/live-pane process start ticks. Treat an already
dead retained pane as dead, not as a missing live process to recreate.
Observe `gemini-1` using read-only capture and metadata; accept genuine idle
completion if work has finished. Never send input to manufacture activity.

- [ ] **Step 3: Record and commit verified deployment**

Check off completed steps and record actual test, process, and configuration
counts in the spec and this plan; do not commit private snapshots.

```bash
git add docs/superpowers/specs/2026-10-28-claude-live-footer-design.md \
  docs/superpowers/plans/2026-10-28-claude-live-footer.md
git commit -m "docs: record verified Claude footer deployment"
```

### Task 4: Push and verify delivery

**Files:** No further runtime changes.

**Interfaces:**
- Consumes: clean tested commits and healthy local deployment.
- Produces: verified matching local and remote delivered heads.

- [ ] **Step 1: Verify ancestry and push without rewriting history**

```bash
test -z "$(git status --porcelain)"
git fetch --no-tags origin main
git merge-base --is-ancestor origin/main HEAD
git push origin HEAD:main
test "$(git rev-parse HEAD)" = \
  "$(git ls-remote --heads origin main | cut -f1)"
```

If remote ancestry has changed, inspect and integrate rather than forcing.
After delivery, mark this step complete, commit that documentation update,
and push/verify it with the same normal procedure.

## Plan Self-Review

- Spec criteria 1–3, 6, and 8: bounded detector tests, positive and negative
  footer cases, approval precedence, agent scoping, and scan propagation.
- Criteria 4, 5, and 7: daemon integration tests cover old idle/Stop evidence,
  genuine completion, and newer completion/approval.
- Criterion 9: red/green focused tests and complete source/staged/installed suites.
- Criteria 10–11: code-only activation, safety/rollback checks, and verified
  fast-forward publication.
- No added runtime dependency, metadata read, hook, completion delay, or
  persisted screen content.
