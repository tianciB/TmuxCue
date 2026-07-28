from __future__ import annotations

import argparse
import json
import sys
import time

from .events import write_event


def entrypoint(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record a Codex or Claude lifecycle event")
    parser.add_argument("--agent", required=True, choices=("codex", "claude"))
    parser.add_argument("--state", required=True, choices=("running", "approval", "done", "ended"))
    args = parser.parse_args(argv)

    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        input_data = {}

    event_name = str(input_data.get("hook_event_name") or "")
    write_event(
        {
            "agent": args.agent,
            "session_id": input_data.get("session_id") or input_data.get("sessionId"),
            "state": args.state,
            "event": event_name,
            "timestamp": time.time(),
        }
    )

    # Codex Stop/UserPromptSubmit hooks accept an empty JSON object as a
    # no-op response. PermissionRequest must remain undecided so the normal
    # approval UI is still shown.
    if args.agent == "codex" and event_name in {"Stop", "UserPromptSubmit", "SessionStart"}:
        print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(entrypoint())
