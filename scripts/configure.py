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


def agy_handler_is_ours(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    if any(marker in str(entry.get("command", "")) for marker in HOOK_MARKERS):
        return True
    hooks = entry.get("hooks")
    if isinstance(hooks, list):
        return any(
            isinstance(item, dict)
            and any(marker in str(item.get("command", "")) for marker in HOOK_MARKERS)
            for item in hooks
        )
    return False


def agy_spec_is_ours(name: str, spec: object) -> bool:
    if not isinstance(spec, dict):
        return False
    if name == "tmuxcue":
        return True
    for key, value in spec.items():
        if key == "enabled":
            continue
        if isinstance(value, list) and any(agy_handler_is_ours(item) for item in value):
            return True
    return False


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Invalid JSON settings file: {path}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid JSON settings file: {path}")
    return value


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


def merge_agy_hooks(path: Path, template: Path) -> None:
    data = load_json(path)
    for name in list(data):
        if agy_spec_is_ours(name, data[name]):
            data.pop(name, None)
    additions = json.loads(template.read_text(encoding="utf-8"))
    for name, spec in additions.items():
        data[name] = spec
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def remove_agy_hooks(path: Path) -> None:
    if not path.exists():
        return
    data = load_json(path)
    changed = False
    for name in list(data):
        if agy_spec_is_ours(name, data[name]):
            data.pop(name, None)
            changed = True
    if not changed:
        return
    if data:
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    else:
        path.unlink()


def tmuxcue_config_dir(home: Path) -> Path:
    if home.resolve() == Path.home().resolve():
        base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    else:
        base = home / ".config"
    return base / "tmuxcue"


def legacy_gemini_registry_path(home: Path) -> Path:
    return tmuxcue_config_dir(home) / "gemini-settings.json"


def safe_legacy_gemini_settings_path(
    path: Path, *, uid: int | None = None
) -> Path | None:
    uid = os.getuid() if uid is None else uid
    try:
        candidate = path.expanduser().resolve()
    except OSError:
        return None
    if (
        not candidate.is_absolute()
        or candidate.name != "settings.json"
        or candidate.parent.name != ".gemini"
    ):
        return None
    owner_target = candidate
    while not owner_target.exists() and owner_target != owner_target.parent:
        owner_target = owner_target.parent
    try:
        if owner_target.stat().st_uid != uid:
            return None
    except OSError:
        return None
    return candidate


def read_legacy_gemini_registry(home: Path) -> set[Path]:
    path = legacy_gemini_registry_path(home)
    if not path.exists():
        return set()
    data = load_json(path)
    values = data.get("paths")
    if not isinstance(values, list):
        return set()
    paths: set[Path] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        candidate = safe_legacy_gemini_settings_path(Path(value))
        if candidate is not None:
            paths.add(candidate)
    return paths


def cleanup_legacy_gemini_hooks(home: Path) -> None:
    paths = read_legacy_gemini_registry(home)
    default = safe_legacy_gemini_settings_path(home / ".gemini/settings.json")
    if default is not None and default.exists():
        paths.add(default)
    for path in sorted(paths):
        if not path.exists():
            continue
        try:
            data = load_json(path)
        except RuntimeError:
            continue
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            continue
        if any(
            hook_is_ours(group)
            for groups in hooks.values()
            if isinstance(groups, list)
            for group in groups
            if isinstance(group, dict)
        ):
            backup(path)
            remove_hooks(path)
    try:
        legacy_gemini_registry_path(home).unlink()
    except FileNotFoundError:
        pass


def install(root: Path, home: Path) -> None:
    tmux_conf = Path(os.environ.get("TMUX_CONF", home / ".tmux.conf"))
    codex_hooks = home / ".codex/hooks.json"
    claude_settings = home / ".claude/settings.json"
    agy_hooks = home / ".gemini/config/hooks.json"

    for path in (codex_hooks, claude_settings, agy_hooks):
        load_json(path)
    for path in (tmux_conf, codex_hooks, claude_settings, agy_hooks):
        backup(path)

    current = tmux_conf.read_text(encoding="utf-8") if tmux_conf.exists() else ""
    block = (root / "tmux/tmuxcue.conf").read_text(encoding="utf-8").strip()
    atomic_write(tmux_conf, without_blocks(current).rstrip() + "\n\n" + block + "\n")
    merge_hooks(codex_hooks, root / "hooks/codex-hooks.json.in", codex=True)
    merge_hooks(claude_settings, root / "hooks/claude-hooks.json.in")
    merge_agy_hooks(agy_hooks, root / "hooks/agy-hooks.json.in")
    cleanup_legacy_gemini_hooks(home)


def uninstall(home: Path) -> None:
    tmux_conf = Path(os.environ.get("TMUX_CONF", home / ".tmux.conf"))
    codex_hooks = home / ".codex/hooks.json"
    claude_settings = home / ".claude/settings.json"
    agy_hooks = home / ".gemini/config/hooks.json"
    for path in (tmux_conf, codex_hooks, claude_settings, agy_hooks):
        backup(path)
    if tmux_conf.exists():
        atomic_write(tmux_conf, without_blocks(tmux_conf.read_text(encoding="utf-8")) + "\n")
    remove_hooks(codex_hooks)
    remove_hooks(claude_settings)
    remove_agy_hooks(agy_hooks)
    cleanup_legacy_gemini_hooks(home)


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
