# Gemini CLI Supervision v0.5.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Gemini CLI as a fully supervised TmuxCue agent with the same state icons, title format, metadata, direct-terminal behavior, session isolation, configuration safety, and deployment guarantees as Codex and Claude.

**Architecture:** Move process identity work into a focused process helper, identify the official Node-based Gemini entry point, and bind Gemini hook events to a process incarnation by PID, start time, and TTY. Resolve model/topic from only the bound Gemini JSONL session, merge observational hooks into safe user settings homes, and reuse the existing shared visual-state and title formatter.

**Tech Stack:** Python 3.10+ standard library, unittest, Linux `/proc`, tmux CLI, JSON/JSONL, Bash, systemd user service.

**Status:** Implemented and locally deployed on 2026-10-15.

## Global Constraints

- Release version is exactly `0.5.0`.
- Runtime dependencies remain empty.
- Python support remains `>=3.10`.
- Gemini uses the same `❓`, rotating tmux moon phases, stable direct-terminal `🌒`, `✅`, `[tmux]`, and `[term]` presentation as Codex and Claude.
- TmuxCue never approves, denies, or modifies Gemini tool permission requests.
- Event files never persist prompt text, model output, tool arguments, permission details, notification messages, transcript paths, or working directories.
- Session binding must require PID, process start time, and TTY; ambiguous fallback returns no session.
- Only current-user user-level Gemini settings are modified; project `.gemini/settings.json` files are never modified.
- No install, update, or uninstall path may send input or signals to an AI process or invoke `send-keys`, `attach`, `respawn`, or tmux kill commands.
- Existing Codex and Claude behavior must remain unchanged.

---

### Task 1: Centralize process identity and recognize Gemini CLI

**Files:**
- Create: `src/tmuxcue/processes.py`
- Modify: `src/tmuxcue/metadata.py:15-130`
- Modify: `src/tmuxcue/detector.py:8-10`
- Modify: `src/tmuxcue/terminal.py:7-10`
- Create: `tests/test_processes.py`
- Modify: `tests/test_detector.py:1-75`
- Modify: `tests/test_terminal.py:1-45`

**Interfaces:**
- Produces: `ProcessInfo`, `ProcessSnapshot`, `ProcessBinding`, `is_gemini_cli_command()`, `discover_all_agent_processes()`, `discover_agent_processes()`, `find_agent_ancestor()`, and `read_process_environment_value()` in `tmuxcue.processes`.
- Consumes: Linux `/proc/<pid>/{comm,stat,cmdline,environ,fd/0}` and current-user UID.

- [x] **Step 1: Add failing strict-classification and foreground-selection tests**

Create `tests/test_processes.py` with fixtures that write a bounded fake `/proc`
tree and assertions equivalent to:

```python
class ProcessDiscoveryTests(unittest.TestCase):
    def test_official_gemini_node_entrypoint_is_detected(self):
        proc = self.proc_tree(
            [
                ProcessFixture(
                    pid=201,
                    comm="node",
                    cmdline=(
                        "node\0/opt/app/node_modules/.bin/gemini\0"
                        "--model\0gemini-test\0"
                    ),
                    tty="/dev/pts/7",
                    process_group=200,
                    foreground_group=200,
                    start_ticks=500,
                )
            ]
        )
        rows = discover_agent_processes(proc, uid=os.getuid(), now=1_000)
        self.assertEqual(rows[("/dev/pts/7", "gemini")][0], 201)

    def test_unrelated_node_command_containing_gemini_is_rejected(self):
        self.assertFalse(
            is_gemini_cli_command("node /srv/gemini-report/server.js")
        )

    def test_deepest_official_gemini_node_layer_wins_one_tty(self):
        proc = self.proc_tree(
            [
                ProcessFixture(
                    pid=201,
                    comm="node",
                    cmdline="node\0/opt/node_modules/.bin/gemini\0",
                    tty="/dev/pts/7",
                    process_group=200,
                    foreground_group=200,
                    start_ticks=500,
                ),
                ProcessFixture(
                    pid=202,
                    comm="node",
                    cmdline=(
                        "node\0--max-old-space-size=4096\0"
                        "/opt/node_modules/.bin/gemini\0"
                    ),
                    tty="/dev/pts/7",
                    process_group=200,
                    foreground_group=200,
                    start_ticks=510,
                ),
            ]
        )
        rows = discover_agent_processes(proc, uid=os.getuid(), now=1_000)
        self.assertEqual(rows[("/dev/pts/7", "gemini")][0], 202)
```

Also add detector and terminal assertions that a provided
`{("/dev/pts/7", "gemini"): (202, 900, "node .../.bin/gemini")}` snapshot
creates a Gemini pane/terminal row.

- [x] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_processes tests.test_detector tests.test_terminal -v
```

Expected: import failure for `tmuxcue.processes` or missing Gemini rows.

- [x] **Step 3: Implement the shared process module**

Create `src/tmuxcue/processes.py` with these concrete public definitions:

```python
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import time

ProcessInfo = tuple[int, int, str]
ProcessSnapshot = dict[tuple[str, str], ProcessInfo]
AGENT_PROCESS_NAMES = {
    "codex": "codex",
    "claude": "claude",
    "claude-code": "claude",
}
GEMINI_ENTRY = re.compile(
    r"(?:^|\s)(?:\S*/)?node_modules/\.bin/gemini(?:\s|$)"
    r"|(?:^|\s)\S*/@google/gemini-cli(?:/|\s|$)"
)


@dataclass(frozen=True)
class ProcessBinding:
    agent: str
    pid: int
    started_at: int
    tty: str
    cmdline: str


def is_gemini_cli_command(command: str) -> bool:
    return bool(GEMINI_ENTRY.search(" ".join((command or "").split())))
```

Move the existing stat/start-time calculation from `metadata.py` into this
module. `discover_all_agent_processes()` must read `cmdline` before
classification, classify exact `codex`/`claude` comm values as before, classify
Node only when `is_gemini_cli_command(cmdline)` succeeds, and return all valid
foreground bindings. `discover_agent_processes()` must select the highest PID
for each `(tty, agent)` key to preserve existing behavior and select the inner
Gemini Node layer.

Implement:

```python
def find_agent_ancestor(
    agent: str,
    *,
    start_pid: int | None = None,
    proc_root: Path = Path("/proc"),
    uid: int | None = None,
) -> ProcessBinding | None:
```

Walk `start_pid or os.getppid()` through stat field 4 (PPID), stop at PID 1 or
a repeated PID, and return the first ancestor whose classified agent equals
`agent`.

Implement:

```python
def read_process_environment_value(
    pid: int,
    name: str,
    *,
    proc_root: Path = Path("/proc"),
    uid: int | None = None,
) -> str:
```

Verify process ownership, split `environ` on NUL bytes, and return only the
requested key's decoded value.

Update imports in metadata, detector, and terminal. Remove the duplicated
process types and discovery implementation from `metadata.py`.

- [x] **Step 4: Run focused tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_processes tests.test_detector tests.test_terminal tests.test_metadata -v
```

Expected: all tests pass and existing Codex/Claude discovery tests remain
green.

- [x] **Step 5: Commit**

```bash
git add src/tmuxcue/processes.py src/tmuxcue/metadata.py \
  src/tmuxcue/detector.py src/tmuxcue/terminal.py \
  tests/test_processes.py tests/test_detector.py tests/test_terminal.py
git commit -m "feat: detect Gemini CLI processes safely"
```

---

### Task 2: Detect Gemini native states with the shared visual presentation

**Files:**
- Modify: `src/tmuxcue/detector.py:11-170`
- Modify: `tests/test_detector.py`
- Modify: `tests/test_daemon.py`

**Interfaces:**
- Consumes: `TmuxDetector.detect_state(agent, title, screen)`.
- Produces: raw Gemini states `blocked`, `working`, and `idle`; final icons
  continue through existing `visual_state()`, `icon_for()`, and
  `IndicatorDaemon.format_title()`.

- [x] **Step 1: Add failing native-title tests**

Add:

```python
def test_gemini_native_titles_map_to_shared_states(self):
    self.assertEqual(
        self.detector.detect_state("gemini", "✦ Working (repo)", ""),
        "working",
    )
    self.assertEqual(
        self.detector.detect_state("gemini", "✋ Action Required (repo)", ""),
        "blocked",
    )
    self.assertEqual(
        self.detector.detect_state("gemini", "◇ Ready (repo)", ""),
        "idle",
    )

def test_gemini_old_permission_text_outside_tail_is_ignored(self):
    screen = "Allow execution?\\n" + "\\n".join(
        f"output {index}" for index in range(13)
    )
    self.assertEqual(
        self.detector.detect_state("gemini", "◇ Ready (repo)", screen),
        "idle",
    )
```

Add a daemon formatting assertion:

```python
row = PaneRow("%3", "gemini:0", "gemini", "working", "/repo", "", "/dev/pts/7")
metadata = Metadata("gemini", "gemini-test", "Fix session binding", "session-1")
icon, title = daemon.format_title(row, "running", metadata, now=0)
self.assertEqual(icon, "🌑")
self.assertEqual(
    title,
    "🌑 [tmux] gemini/gemini-test: Fix session binding",
)
```

- [x] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_detector tests.test_daemon -v
```

Expected: Gemini `✦` and `✋` titles currently resolve to idle.

- [x] **Step 3: Implement Gemini state patterns**

Add:

```python
GEMINI_APPROVAL_PATTERNS = (
    r"allow execution",
    r"action required",
    r"waiting for approval",
)
GEMINI_WORKING_PATTERNS = (
    r"(?:esc|ctrl\\+c) to (?:cancel|interrupt)",
    r"working",
)
```

In `detect_state()`:

1. Return `blocked` when Gemini title starts with `✋` or contains
   `action required`.
2. Return `working` when Gemini title starts with `✦` or contains `working`.
3. Return `idle` for `◇ Ready` unless the bounded screen tail contains a
   current approval/working pattern.
4. Keep Codex braille and Claude behavior unchanged.
5. Select approval and working pattern tuples by explicit agent rather than
   treating every non-Codex agent as Claude.

- [x] **Step 4: Run focused tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_detector tests.test_daemon -v
```

Expected: all tests pass, including exact Gemini title formatting.

- [x] **Step 5: Commit**

```bash
git add src/tmuxcue/detector.py tests/test_detector.py tests/test_daemon.py
git commit -m "feat: map Gemini native states to TmuxCue"
```

---

### Task 3: Record observational Gemini lifecycle events and process bindings

**Files:**
- Create: `hooks/gemini-hooks.json.in`
- Modify: `src/tmuxcue/hook.py`
- Modify: `tests/test_events.py`
- Modify: `tests/test_install_safety.py`

**Interfaces:**
- Consumes: Gemini hook JSON on stdin and
  `find_agent_ancestor("gemini")`.
- Produces: event JSON containing only `agent`, `session_id`, `state`, `event`,
  `timestamp`, `pid`, `started_at`, and `tty`.

- [x] **Step 1: Add failing hook template, output, binding, and privacy tests**

Add tests that load the template and require exactly:

```python
expected = {
    "SessionStart": ("done", None),
    "BeforeAgent": ("running", None),
    "Notification": ("approval", "ToolPermission"),
    "AfterAgent": ("done", None),
    "SessionEnd": ("ended", None),
}
```

For `hook.entrypoint()` patch stdin with:

```python
{
    "session_id": "11111111-1111-1111-1111-111111111111",
    "transcript_path": "/private/transcript.jsonl",
    "cwd": "/private/project",
    "hook_event_name": "Notification",
    "notification_type": "ToolPermission",
    "message": "private permission message",
    "details": {"tool_name": "run_shell_command", "path": "/private/file"},
}
```

Patch `find_agent_ancestor()` to return:

```python
ProcessBinding(
    agent="gemini",
    pid=321,
    started_at=900,
    tty="/dev/pts/7",
    cmdline="node /opt/node_modules/.bin/gemini",
)
```

Assert stdout is exactly `"{}\\n"` and the persisted event has the allowed
keys only.

- [x] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_events tests.test_install_safety -v
```

Expected: `--agent gemini` is rejected and the template is absent.

- [x] **Step 3: Add the Gemini hook template**

Create `hooks/gemini-hooks.json.in` with five groups. Each command must be
quoted exactly as:

```json
{
  "type": "command",
  "name": "tmuxcue-before-agent",
  "command": "\"$HOME/.local/bin/tmuxcue-hook\" --agent gemini --state running",
  "timeout": 5000
}
```

Use event-specific names, omit matchers for the four all-event hooks, and set
`"matcher": "ToolPermission"` only on `Notification`.

- [x] **Step 4: Extend the hook entrypoint**

Change the parser choices to include `gemini`. Resolve a process binding only
for Gemini:

```python
binding = find_agent_ancestor("gemini") if args.agent == "gemini" else None
payload = {
    "agent": args.agent,
    "session_id": input_data.get("session_id") or input_data.get("sessionId"),
    "state": args.state,
    "event": event_name,
    "timestamp": time.time(),
}
if binding is not None:
    payload.update(
        {
            "pid": binding.pid,
            "started_at": binding.started_at,
            "tty": binding.tty,
        }
    )
write_event(payload)
```

Print `{}` for every Gemini event. Keep the existing Codex no-op response
behavior and Claude silence unchanged.

- [x] **Step 5: Run focused tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_events tests.test_install_safety -v
```

Expected: all event/template/privacy tests pass.

- [x] **Step 6: Commit**

```bash
git add hooks/gemini-hooks.json.in src/tmuxcue/hook.py \
  tests/test_events.py tests/test_install_safety.py
git commit -m "feat: record Gemini lifecycle hooks"
```

---

### Task 4: Resolve only the bound Gemini session's model and topic

**Files:**
- Create: `src/tmuxcue/gemini.py`
- Modify: `src/tmuxcue/metadata.py:144-585`
- Create: `tests/test_gemini.py`
- Modify: `tests/test_metadata.py`

**Interfaces:**
- Produces: `GeminiSession`, `gemini_state_dir()`,
  `load_gemini_sessions()`, `select_gemini_session()`,
  `gemini_model_from_command()`, and `gemini_configured_model()`.
- Extends: `MetadataResolver.resolve(row, processes=None, hook_events=None)`.

- [x] **Step 1: Add failing JSONL parsing and isolation tests**

Build Gemini state fixtures with:

```json
{"sessionId":"11111111-1111-1111-1111-111111111111","projectHash":"repo","startTime":"2026-10-15T07:00:00Z","lastUpdated":"2026-10-15T07:05:00Z","kind":"main"}
{"id":"u1","timestamp":"2026-10-15T07:00:01Z","type":"user","content":[{"text":"Implement Gemini supervision"}]}
{"id":"g1","timestamp":"2026-10-15T07:01:00Z","type":"gemini","model":"gemini-test","content":"done"}
```

Cover:

- exact event PID/start-time/TTY selects the matching UUID;
- same PID with a different start time is rejected;
- same TTY with a different PID is rejected;
- two same-project fallback candidates return no session;
- one compatible same-project candidate is accepted;
- custom `GEMINI_CLI_HOME` points to `<value>/.gemini`;
- unrelated environment variables are never requested;
- model comes from the bound transcript;
- generic latest input such as `OK` does not replace the previous meaningful
  topic;
- `--model gemini-command` and `--model=gemini-command` are parsed;
- malformed or larger-than-2-MiB transcripts are skipped.

- [x] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_gemini tests.test_metadata -v
```

Expected: missing `tmuxcue.gemini` and no Gemini metadata branch.

- [x] **Step 3: Implement bounded Gemini session parsing**

Create:

```python
@dataclass(frozen=True)
class GeminiSession:
    path: Path
    session_id: str
    project_root: str
    started_at: float
    updated_at: float
    model: str
    topic: str
```

`load_gemini_sessions(state_dir, limit=200, max_bytes=2 * 1024 * 1024)` must:

1. Glob `tmp/*/chats/session-*.jsonl`.
2. Sort by mtime descending and inspect at most 200 files.
3. Reject files over the byte limit.
4. Read the project root from `path.parent.parent / ".project_root"`.
5. Require the first valid object to contain a UUID `sessionId`.
6. Track the latest non-empty Gemini `model`.
7. Track the latest non-generic user `content[*].text`.
8. Return records without exposing model output content.

`gemini_state_dir()` reads only `GEMINI_CLI_HOME` through
`read_process_environment_value()`, accepts an absolute current-user-owned
directory, and otherwise returns `home / ".gemini"`.

`select_gemini_session()` first accepts an exact session ID. Without one, it
filters by exact project root and requires exactly one session whose
`updated_at >= process_started_at - 5`.

- [x] **Step 4: Bind hook events and integrate metadata resolution**

Add:

```python
def gemini_bound_session_id(
    events: dict[tuple[str, str], dict],
    process: ProcessInfo,
    tty: str,
) -> str:
```

Choose the newest Gemini event only when `pid`, `started_at`, and `tty` all
match and the event timestamp is at least the process start.

Extend `MetadataResolver.resolve()` with optional `hook_events`. For
`row.agent == "gemini"`, call:

```python
model, topic, session_id = self.gemini_metadata(
    cwd=cwd,
    tty=row.tty,
    process=process,
    hook_events=hook_events or {},
)
```

Model fallback order is transcript, command line, then
`settings.json["model"]["name"]`. Topic fallback remains the shared pane
subject/project basename only after Gemini returns no bound topic.

- [x] **Step 5: Run focused tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_gemini tests.test_metadata tests.test_privacy -v
```

Expected: all tests pass and no fixture contains personal runtime data.

- [x] **Step 6: Commit**

```bash
git add src/tmuxcue/gemini.py src/tmuxcue/metadata.py \
  tests/test_gemini.py tests/test_metadata.py
git commit -m "feat: bind Gemini metadata to exact sessions"
```

---

### Task 5: Feed Gemini hook bindings through the daemon

**Files:**
- Modify: `src/tmuxcue/service.py:248-585`
- Modify: `tests/test_daemon.py`

**Interfaces:**
- Changes: `refresh_metadata_targets(targets, now, hook_events=None)`.
- Consumes: `MetadataResolver.resolve(..., hook_events=hook_events)`.
- Produces: exact Gemini metadata before hook state lookup in
  `apply_targets()`.

- [x] **Step 1: Add failing daemon binding and state-precedence tests**

Create daemon tests with two Gemini rows sharing `/repo` but different
PID/start-time/TTY values. Provide two hook events and a fake resolver that
returns a distinct session per exact event binding. Assert:

- each target receives its own session ID;
- a newer Gemini approval event resolves to `approval`;
- newer live `working` observation replaces an older approval event;
- newer `AfterAgent` done event resolves to `done`;
- a done event does not replace newer live working evidence;
- direct-terminal running uses stable `🌒`.

- [x] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_daemon -v
```

Expected: resolver calls do not receive hook events, so same-directory Gemini
rows cannot bind independently.

- [x] **Step 3: Pass hook events during metadata refresh**

Change:

```python
def refresh_metadata_targets(
    self,
    targets: set[str],
    now: float,
    hook_events: dict[tuple[str, str], dict] | None = None,
) -> set[str]:
```

Call:

```python
metadata = self.metadata_resolver.resolve(
    row,
    processes=processes,
    hook_events=hook_events or {},
)
```

In `update_once()`, pass the already-polled `hook_events` to
`refresh_metadata_targets()`. Keep existing state precedence; only add tests
where necessary because `resolve_state()` already allows newer live
observations to replace stale approval/done events.

- [x] **Step 4: Run focused daemon tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_daemon tests.test_metadata -v
```

Expected: all daemon and metadata tests pass.

- [x] **Step 5: Commit**

```bash
git add src/tmuxcue/service.py tests/test_daemon.py
git commit -m "feat: bind Gemini hooks in the daemon"
```

---

### Task 6: Merge Gemini hooks and preserve Gemini title settings

**Files:**
- Modify: `scripts/configure.py`
- Modify: `src/tmuxcue/settings.py`
- Modify: `tests/test_install_safety.py`
- Modify: `tests/test_settings.py`

**Interfaces:**
- Produces: `gemini_settings_registry_path()`,
  `managed_gemini_settings_paths()`, and reversible Gemini UI title handling.
- Extends: `configure.install(root, home, gemini_settings_paths=None)` for
  deterministic tests.

- [x] **Step 1: Add failing reversible-configuration tests**

Extend configuration tests to create:

```python
gemini_settings = home / ".gemini/settings.json"
gemini_settings.parent.mkdir()
gemini_settings.write_text(
    json.dumps(
        {
            "model": {"name": "gemini-test"},
            "hooks": {
                "AfterAgent": [
                    {
                        "hooks": [
                            {"type": "command", "command": "/usr/bin/true"}
                        ]
                    }
                ]
            },
        }
    ),
    encoding="utf-8",
)
```

Call `install(..., gemini_settings_paths={gemini_settings})` and assert the
existing hook remains, all five TmuxCue groups exist, and the registry records
the absolute path. After uninstall, assert only the existing hook remains.

Add malformed-JSON coverage that expects `RuntimeError` and unchanged bytes.

Extend title tests with original:

```json
{
  "ui": {
    "dynamicWindowTitle": true,
    "showStatusInTitle": true,
    "theme": "Default"
  }
}
```

Assert override sets both title keys to false and native restores both true
while preserving `theme`.

- [x] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_install_safety tests.test_settings -v
```

Expected: install does not touch Gemini settings and title mode does not
manage Gemini UI keys.

- [x] **Step 3: Implement safe Gemini settings discovery and registry**

In `scripts/configure.py`:

1. Always include `home / ".gemini/settings.json"`.
2. Discover selected current-user Gemini processes through
   `discover_agent_processes()`.
3. Read only `GEMINI_CLI_HOME`; accept only absolute current-user-owned
   directories; append `<home>/.gemini/settings.json`.
4. Allow the optional explicit set in tests.
5. Back up existing files.
6. Merge `hooks/gemini-hooks.json.in`.
7. Atomically write the registry to
   `~/.config/tmuxcue/gemini-settings.json` with mode `0600`.
8. On uninstall, validate and read the registry, remove only TmuxCue hook
   groups, then remove the registry.

Raise:

```python
RuntimeError(f"Invalid JSON settings file: {path}")
```

instead of replacing malformed JSON.

- [x] **Step 4: Implement reversible Gemini title settings**

In `settings.py`, read validated registry paths. Save state under:

```python
state["gemini"][str(path)] = {
    "dynamicWindowTitle": {
        "present": "dynamicWindowTitle" in ui,
        "value": ui.get("dynamicWindowTitle"),
    },
    "showStatusInTitle": {
        "present": "showStatusInTitle" in ui,
        "value": ui.get("showStatusInTitle"),
    },
}
```

Override sets both keys to `False`. Restore changes a key only when its current
value is still `False`, restoring the saved value or removing a key that
TmuxCue added. Remove `ui` only when it becomes empty.

- [x] **Step 5: Run focused configuration tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_install_safety tests.test_settings -v
```

Expected: all configuration, malformed-input, and reversible-title tests pass.

- [x] **Step 6: Commit**

```bash
git add scripts/configure.py src/tmuxcue/settings.py \
  tests/test_install_safety.py tests/test_settings.py
git commit -m "feat: configure Gemini hooks and titles safely"
```

---

### Task 7: Include Gemini in install/update/uninstall process safety

**Files:**
- Modify: `src/tmuxcue/service.py:615-725`
- Modify: `scripts/install.sh`
- Modify: `scripts/update.sh`
- Modify: `scripts/uninstall.sh`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_install_safety.py`

**Interfaces:**
- Produces: hidden command `tmuxcue __process-snapshot`.
- Consumes: `discover_all_agent_processes()`.

- [x] **Step 1: Add failing snapshot and script tests**

Add CLI coverage that patches `discover_all_agent_processes()` and requires
sorted lines in this exact format:

```text
gemini:202:900:/dev/pts/7
```

Update script assertions so every safety script obtains both `AI_BEFORE` and
`AI_AFTER` from:

```bash
PYTHONPATH="$ROOT/src" "$PYTHON_BIN" -m tmuxcue __process-snapshot
```

and no longer contains the old `ps ... comm ... awk` filter.

- [x] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_cli tests.test_install_safety -v
```

Expected: hidden command is absent and scripts still omit Node-based Gemini.

- [x] **Step 3: Implement the hidden process snapshot**

Add:

```python
def print_process_snapshot() -> int:
    bindings = discover_all_agent_processes()
    for item in sorted(
        bindings,
        key=lambda value: (value.agent, value.pid, value.started_at, value.tty),
    ):
        print(f"{item.agent}:{item.pid}:{item.started_at}:{item.tty}")
    return 0
```

Dispatch `__process-snapshot` before the public argparse parser so it remains
hidden from help and shell completions.

- [x] **Step 4: Replace shell process filters**

In all three safety scripts, calculate `AI_BEFORE` and `AI_AFTER` with the
hidden command and the already validated Python interpreter. Preserve all tmux
server and pane checks and all forbidden-command guarantees.

- [x] **Step 5: Run safety tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_cli tests.test_install_safety -v
```

Expected: all tests pass and public help still exposes no hidden command.

- [x] **Step 6: Commit**

```bash
git add src/tmuxcue/service.py scripts/install.sh scripts/update.sh \
  scripts/uninstall.sh tests/test_cli.py tests/test_install_safety.py
git commit -m "fix: protect Gemini processes during lifecycle operations"
```

---

### Task 8: Update documentation, descriptions, and release version

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `pyproject.toml`
- Modify: `src/tmuxcue/__init__.py`
- Modify: `src/tmuxcue/hook.py`
- Modify: `src/tmuxcue/service.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_privacy.py`
- Modify: `tests/test_install_safety.py`

**Interfaces:**
- Produces: public v0.5.0 documentation and CLI copy.

- [x] **Step 1: Add failing release/documentation assertions**

Require:

- CLI version `TmuxCue 0.5.0`;
- project/CLI/hook descriptions name Codex, Claude, and Gemini;
- README contains a Gemini example title;
- README lists all five Gemini hooks and exact commands;
- README explains `ToolPermission` is observational only;
- README describes custom `GEMINI_CLI_HOME`;
- architecture detection/output sections include Gemini;
- privacy test remains free of personal paths and CJK characters.

- [x] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_cli tests.test_install_safety tests.test_privacy -v
```

Expected: old version and two-agent wording fail.

- [x] **Step 3: Update version and user-facing copy**

Set both package version sources to `0.5.0`. Change descriptions to:

```text
Safe status titles for Codex, Claude, and Gemini terminals
```

Update the title-mode message to state that native generation is disabled for
newly started Codex, Claude, and Gemini sessions.

- [x] **Step 4: Update README and architecture**

Document:

- identical state indicators across all three agents;
- example `✅ [tmux] gemini/gemini-2.5-pro: ...`;
- five Gemini hooks and exact commands;
- observational permission behavior;
- default and custom Gemini user settings;
- native versus override title handling;
- process/session isolation;
- install and uninstall safety including Node-based Gemini;
- no process restarts or signals.

- [x] **Step 5: Run documentation and full unit tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Expected: all tests pass.

- [x] **Step 6: Commit**

```bash
git add README.md docs/ARCHITECTURE.md pyproject.toml \
  src/tmuxcue/__init__.py src/tmuxcue/hook.py src/tmuxcue/service.py \
  tests/test_cli.py tests/test_privacy.py tests/test_install_safety.py
git commit -m "docs: release Gemini supervision v0.5.0"
```

---

### Task 9: Regression verification and non-disruptive local deployment

**Files:**
- Modify after verification: `docs/superpowers/specs/2026-10-15-gemini-cli-supervision-design.md`
- Modify after verification: `docs/superpowers/plans/2026-10-15-gemini-cli-supervision-v0.5.0.md`

**Interfaces:**
- Consumes: complete v0.5.0 implementation.
- Produces: test evidence, local installation, and deployment status records.

- [x] **Step 1: Verify the repository**

Run:

```bash
git diff --check
PYTHONPATH=src python3 -m tmuxcue __check
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Expected: no whitespace errors, dependency check succeeds, and all tests pass.

- [x] **Step 2: Capture independent pre-deployment identities**

Run read-only snapshots:

```bash
TMUX_BEFORE="$(pgrep -o -u "$(id -u)" tmux || true)"
PANES_BEFORE="$(tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}:#{pane_pid}' 2>/dev/null | sort || true)"
AI_BEFORE="$(PYTHONPATH=src python3 -m tmuxcue __process-snapshot)"
```

Do not run any process-control command.

- [x] **Step 3: Install locally**

Run:

```bash
./install.sh
```

Expected:

```text
TmuxCue installed. Existing tmux and terminal AI processes were left untouched.
```

- [x] **Step 4: Verify identities and service health**

Run:

```bash
systemctl --user is-active --quiet tmuxcue.service
test "$TMUX_BEFORE" = "$(pgrep -o -u "$(id -u)" tmux || true)"
test "$PANES_BEFORE" = "$(tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}:#{pane_pid}' 2>/dev/null | sort || true)"
test "$AI_BEFORE" = "$(PYTHONPATH=src python3 -m tmuxcue __process-snapshot)"
```

Expected: all commands return zero.

- [x] **Step 5: Verify the live Gemini pane read-only**

Use only:

```bash
tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}|#{pane_tty}|#{@agent_state}|#{@agent_icon}|#{@agent_title}'
```

Expected: the Gemini pane has a TmuxCue state/icon/title using
`[tmux] gemini/...`; no key is sent and no pane is attached.

- [x] **Step 6: Record deployment status**

Change the design status to `Implemented and locally deployed`, check every
completed plan checkbox, and add a verification paragraph containing only
generic command results and counts, never local paths, session topics, or
process IDs.

- [x] **Step 7: Commit deployment record**

```bash
git add docs/superpowers/specs/2026-10-15-gemini-cli-supervision-design.md \
  docs/superpowers/plans/2026-10-15-gemini-cli-supervision-v0.5.0.md
git commit -m "docs: record v0.5.0 deployment"
```

## Execution record

- Dependency and tmux checks passed.
- All 96 unit tests passed.
- Local installation completed with TmuxCue v0.5.0 active.
- The tmux server, 54 pane identities, and 11 detected AI process identities
  matched exactly before and after installation.
- The live Gemini pane used the shared done icon and
  `[tmux] gemini/MODEL: TOPIC` layout.
- Two managed Gemini user settings files each contained all five observational
  TmuxCue hooks.
