#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil

BLOCKS = (("# BEGIN tmuxcue", "# END tmuxcue"),)
HOOK_MARKERS = ("tmuxcue-hook", "tmuxcue.hook")


def backup(path: Path) -> None:
    if not path.exists():
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.backup-{stamp}")
    shutil.copy2(path, target)
    os.chmod(target, 0o600)
    print(f"backup: {target}")


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmuxcue-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def without_blocks(text: str) -> str:
    for begin, end in BLOCKS:
        while begin in text and end in text:
            start = text.index(begin)
            stop = text.index(end, start) + len(end)
            text = text[:start].rstrip() + "\n" + text[stop:].lstrip()
    return text.strip()


def hook_is_ours(group: dict) -> bool:
    return any(
        any(marker in str(hook.get("command", "")) for marker in HOOK_MARKERS)
        for hook in group.get("hooks", [])
    )


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def merge_hooks(path: Path, template: Path, codex: bool = False) -> None:
    data = load_json(path)
    if codex:
        data.setdefault("description", "User lifecycle hooks.")
    hooks = data.setdefault("hooks", {})
    for event in list(hooks):
        hooks[event] = [group for group in hooks[event] if not hook_is_ours(group)]
        if not hooks[event]:
            hooks.pop(event)
    additions = json.loads(template.read_text(encoding="utf-8"))
    for event, groups in additions.items():
        hooks[event] = hooks.get(event, []) + groups
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def remove_hooks(path: Path) -> None:
    if not path.exists():
        return
    data = load_json(path)
    hooks = data.get("hooks") or {}
    for event in list(hooks):
        hooks[event] = [group for group in hooks[event] if not hook_is_ours(group)]
        if not hooks[event]:
            hooks.pop(event)
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def install(root: Path, home: Path) -> None:
    tmux_conf = Path(os.environ.get("TMUX_CONF", home / ".tmux.conf"))
    codex_hooks = home / ".codex/hooks.json"
    claude_settings = home / ".claude/settings.json"
    for path in (tmux_conf, codex_hooks, claude_settings):
        backup(path)

    current = tmux_conf.read_text(encoding="utf-8") if tmux_conf.exists() else ""
    block = (root / "tmux/tmuxcue.conf").read_text(encoding="utf-8").strip()
    atomic_write(tmux_conf, without_blocks(current).rstrip() + "\n\n" + block + "\n")
    merge_hooks(codex_hooks, root / "hooks/codex-hooks.json.in", codex=True)
    merge_hooks(claude_settings, root / "hooks/claude-hooks.json.in")


def uninstall(home: Path) -> None:
    tmux_conf = Path(os.environ.get("TMUX_CONF", home / ".tmux.conf"))
    codex_hooks = home / ".codex/hooks.json"
    claude_settings = home / ".claude/settings.json"
    for path in (tmux_conf, codex_hooks, claude_settings):
        backup(path)
    if tmux_conf.exists():
        atomic_write(tmux_conf, without_blocks(tmux_conf.read_text(encoding="utf-8")) + "\n")
    remove_hooks(codex_hooks)
    remove_hooks(claude_settings)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    home = Path.home()
    install(args.root.resolve(), home) if args.action == "install" else uninstall(home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
