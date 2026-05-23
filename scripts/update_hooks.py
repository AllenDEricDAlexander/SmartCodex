#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import filecmp
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "hooks" / "hooks.manifest.json"
HOOKS_JSON_PATH = Path.home() / ".codex" / "hooks.json"
MANAGED_MARKER = "SMARTCODEX_MANAGED_HOOK="


class HookUpdateError(Exception):
    pass


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    with path.open(encoding="utf-8") as f:
        manifest = json.load(f)

    if manifest.get("schema_version") != 1:
        raise HookUpdateError("unsupported manifest schema_version")
    hooks = manifest.get("hooks")
    if not isinstance(hooks, list) or not hooks:
        raise HookUpdateError("manifest must contain at least one hook")
    return manifest


def hooks_by_id(manifest: dict) -> dict[str, dict]:
    result = {}
    for hook in manifest["hooks"]:
        hook_id = hook.get("id")
        if not hook_id:
            raise HookUpdateError("manifest hook is missing id")
        if hook_id in result:
            raise HookUpdateError(f"duplicate manifest hook id: {hook_id}")
        result[hook_id] = hook
    return result


def select_hooks(manifest: dict, hook_ids: list[str]) -> list[dict]:
    available = hooks_by_id(manifest)
    selected_ids = hook_ids or list(available)
    unknown = [hook_id for hook_id in selected_ids if hook_id not in available]
    if unknown:
        raise HookUpdateError("unknown hook id: " + ", ".join(unknown))
    return [available[hook_id] for hook_id in selected_ids]


def expand_target_dir(value: str | None, manifest: dict) -> Path:
    target = value or manifest["default_target_dir"]
    return Path(os.path.expanduser(target)).resolve()


def read_hooks_json(path: Path = HOOKS_JSON_PATH) -> dict:
    if not path.exists():
        return {"hooks": {}}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise HookUpdateError("hooks.json must contain a JSON object")
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise HookUpdateError("hooks.json hooks field must contain a JSON object")
    return data


def is_managed_command(command: str, hook_id: str | None = None) -> bool:
    marker = MANAGED_MARKER if hook_id is None else f"{MANAGED_MARKER}{hook_id}"
    return marker in command


def command_has_managed_marker(command: str, hook_id: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    return f"{MANAGED_MARKER}{hook_id}" in parts


def command_references_target(command: str, target: Path) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    target_resolved = target.resolve()
    for part in parts:
        if part == str(target):
            return True
        candidate = Path(part).expanduser()
        if candidate.is_absolute() and candidate.resolve() == target_resolved:
            return True
    return False


def command_parts(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return []


def is_python_command(part: str) -> bool:
    executable = Path(part).name
    return executable == "python" or executable == "python3" or executable.startswith("python3.")


def python_script_candidates(parts: list[str]) -> list[str]:
    candidates = []
    for index, part in enumerate(parts):
        if not is_python_command(part):
            continue
        for candidate in parts[index + 1 :]:
            if candidate.startswith("-"):
                if candidate == "-m":
                    break
                continue
            candidates.append(candidate)
            break
    return candidates


def path_matches_adoptable_target(value: str, hook: dict, target: Path) -> bool:
    target_resolved = target.resolve()
    recognized_paths = {str(target), str(target_resolved)}
    default_target = Path.home() / ".codex" / "hooks" / hook["target"]
    if default_target.resolve() == target_resolved:
        recognized_paths.update(
            {
                str(default_target),
                f"$HOME/.codex/hooks/{hook['target']}",
                f"${{HOME}}/.codex/hooks/{hook['target']}",
                f"~/.codex/hooks/{hook['target']}",
            }
        )
    if value in recognized_paths:
        return True
    candidate = Path(os.path.expandvars(value)).expanduser()
    return candidate.is_absolute() and candidate.resolve() == target_resolved


def command_references_adoptable_target(command: str, hook: dict, target: Path) -> bool:
    parts = command_parts(command)
    for candidate in python_script_candidates(parts):
        if path_matches_adoptable_target(candidate, hook, target):
            return True
    return False


def legacy_duplicate_entries(config: dict, selected_hooks: list[dict], target_dir: Path) -> list[dict]:
    entries = []
    for hook in selected_hooks:
        target = target_dir / hook["target"]
        hook_events = set(hook["events"])
        for event, groups in config.get("hooks", {}).items():
            if event not in hook_events:
                continue
            for group in groups:
                for hook_config in group.get("hooks", []):
                    command = hook_config.get("command", "")
                    if is_managed_command(command):
                        continue
                    if command_references_adoptable_target(command, hook, target):
                        entries.append({"hook_id": hook["id"], "event": event, "command": command})
    return entries


def managed_hook_targets(config: dict, selected_hooks: list[dict], target_dir: Path) -> set[tuple[str, Path]]:
    managed_targets = set()
    for hook in selected_hooks:
        hook_id = hook["id"]
        target = target_dir / hook["target"]
        for groups in config.get("hooks", {}).values():
            for group in groups:
                for hook_config in group.get("hooks", []):
                    command = hook_config.get("command", "")
                    if command_has_managed_marker(command, hook_id) and command_references_target(command, target):
                        managed_targets.add((hook_id, target))
    return managed_targets


def remove_managed_entries(config: dict, hook_ids: set[str], duplicate_entries: list[dict] | None = None) -> None:
    hooks_config = config.setdefault("hooks", {})
    duplicate_commands = {}
    for entry in duplicate_entries or []:
        duplicate_commands.setdefault((entry["event"], entry["hook_id"]), set()).add(entry["command"])
    for event in list(hooks_config):
        kept_groups = []
        for group in hooks_config[event]:
            kept_hooks = []
            for hook in group.get("hooks", []):
                command = hook.get("command", "")
                if any(is_managed_command(command, hook_id) for hook_id in hook_ids):
                    continue
                if any(command in duplicate_commands.get((event, hook_id), set()) for hook_id in hook_ids):
                    continue
                kept_hooks.append(hook)
            if kept_hooks:
                next_group = dict(group)
                next_group["hooks"] = kept_hooks
                kept_groups.append(next_group)
        if kept_groups:
            hooks_config[event] = kept_groups
        else:
            del hooks_config[event]


def hook_command(hook: dict, target_dir: Path) -> str:
    target_path = target_dir / hook["target"]
    template = hook["command_template"]
    return template.format(id=hook["id"], target_path=str(target_path))


def source_target_drift(hook: dict, target_dir: Path) -> dict | None:
    source = ROOT / "hooks" / hook["source"]
    target = target_dir / hook["target"]
    if not source.exists() or not target.exists():
        return None
    if filecmp.cmp(source, target, shallow=False):
        return None
    return {"hook": hook, "source": source, "target": target}


def managed_source_target_drifts(
    selected_hooks: list[dict],
    target_dir: Path,
    managed_targets: set[tuple[str, Path]],
) -> list[dict]:
    drifts = []
    for hook in selected_hooks:
        target = target_dir / hook["target"]
        if (hook["id"], target) not in managed_targets:
            continue
        drift = source_target_drift(hook, target_dir)
        if drift:
            drifts.append(drift)
    return drifts


def add_managed_entries(config: dict, selected_hooks: list[dict], target_dir: Path) -> None:
    hooks_config = config.setdefault("hooks", {})
    for hook in selected_hooks:
        command = hook_command(hook, target_dir)
        command_hook = {
            "type": "command",
            "command": command,
            "timeout": hook.get("timeout", 5),
        }
        for event in hook["events"]:
            hooks_config.setdefault(event, []).append({"hooks": [command_hook]})


def backup_hooks_json(path: Path) -> Path | None:
    if not path.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    backup_path = path.with_name(f"{path.name}.smartcodex-backup-{timestamp}")
    shutil.copy2(path, backup_path)
    return backup_path


def backup_file(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    backup_path = path.with_name(f"{path.name}.smartcodex-backup-{timestamp}")
    shutil.copy2(path, backup_path)
    return backup_path


def write_hooks_json(config: dict, path: Path = HOOKS_JSON_PATH, create_backup: bool = True) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = backup_hooks_json(path) if create_backup else None
    with path.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return backup_path


def install_plan(
    config: dict,
    selected_hooks: list[dict],
    target_dir: Path,
    managed_targets: set[tuple[str, Path]],
    adopt_existing: bool,
) -> tuple[list[dict], list[dict]]:
    adoptions = []
    duplicate_entries = legacy_duplicate_entries(config, selected_hooks, target_dir)
    if target_dir.exists() and not target_dir.is_dir():
        raise HookUpdateError(f"hook target directory is not a directory: {target_dir}")

    for hook in selected_hooks:
        source = ROOT / "hooks" / hook["source"]
        target = target_dir / hook["target"]
        if not source.exists():
            raise HookUpdateError(f"hook source does not exist: {source}")
        target_is_managed = (hook["id"], target) in managed_targets
        if not target.exists() or target_is_managed or filecmp.cmp(source, target, shallow=False):
            continue
        if not adopt_existing:
            raise HookUpdateError(
                f"refusing to overwrite unmanaged hook target: {target}; "
                "rerun with --adopt-existing to adopt a recognized SmartCodex hook"
            )
        if not any(entry["hook_id"] == hook["id"] for entry in duplicate_entries):
            raise HookUpdateError(f"refusing to adopt unrecognized unmanaged hook target: {target}")
        adoptions.append({"hook": hook, "source": source, "target": target})

    return adoptions, duplicate_entries


def install_hook_files(selected_hooks: list[dict], target_dir: Path, managed_targets: set[tuple[str, Path]]) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for hook in selected_hooks:
        source = ROOT / "hooks" / hook["source"]
        target = target_dir / hook["target"]
        if not source.exists():
            raise HookUpdateError(f"hook source does not exist: {source}")
        target_is_managed = (hook["id"], target) in managed_targets
        if target.exists() and not target_is_managed and not filecmp.cmp(source, target, shallow=False):
            raise HookUpdateError(f"refusing to overwrite unmanaged hook target: {target}")
        shutil.copy2(source, target)
        mode = target.stat().st_mode
        target.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def print_legacy_duplicate(entry: dict, prefix: str = "duplicate") -> None:
    print(f"{prefix} legacy {entry['hook_id']} hook for {entry['event']}: {entry['command']}")


def print_dry_run_install(
    selected_hooks: list[dict],
    target_dir: Path,
    adoptions: list[dict],
    duplicate_entries: list[dict],
    managed_drifts: list[dict],
    adopt_existing: bool,
) -> None:
    for drift in managed_drifts:
        print(f"would replace managed target: {drift['target']}")
        print(f"source differs from installed target: {drift['source']}")
    if adopt_existing:
        for adoption in adoptions:
            hook = adoption["hook"]
            target = adoption["target"]
            print(f"would adopt existing {hook['id']} -> {target}")
            print(f"would backup target: {target}.smartcodex-backup-<timestamp>")
            print(f"would replace target: {target}")
        for entry in sorted(duplicate_entries, key=lambda item: (item["event"], item["hook_id"], item["command"])):
            print_legacy_duplicate(entry, prefix="would remove duplicate")
        if adoptions or duplicate_entries:
            print(f"would backup hooks.json: {HOOKS_JSON_PATH}.smartcodex-backup-<timestamp>")
            print(f"would update hooks.json: {HOOKS_JSON_PATH}")
    else:
        for entry in sorted(duplicate_entries, key=lambda item: (item["event"], item["hook_id"], item["command"])):
            print_legacy_duplicate(entry)
        if duplicate_entries:
            print("rerun with --adopt-existing to remove recognized duplicate legacy SmartCodex hooks")
    for hook in selected_hooks:
        print(f"would install {hook['id']} -> {target_dir / hook['target']}")


def hook_is_enabled(config: dict, hook_id: str) -> bool:
    for groups in config.get("hooks", {}).values():
        for group in groups:
            for hook in group.get("hooks", []):
                if is_managed_command(hook.get("command", ""), hook_id):
                    return True
    return False


def print_hook_list(manifest: dict) -> None:
    for hook in manifest["hooks"]:
        print(f"{hook['id']}: {hook.get('description', '')}".rstrip())


def print_status(manifest: dict, selected_hooks: list[dict], target_dir: Path) -> None:
    config = read_hooks_json()
    duplicate_entries = legacy_duplicate_entries(config, selected_hooks, target_dir)
    for hook in selected_hooks:
        installed = (target_dir / hook["target"]).exists()
        enabled = hook_is_enabled(config, hook["id"])
        drift = source_target_drift(hook, target_dir)
        state = []
        state.append("installed" if installed else "not installed")
        state.append("enabled" if enabled else "disabled")
        if drift:
            state.append("source drift")
        duplicates = [entry for entry in duplicate_entries if entry["hook_id"] == hook["id"]]
        if duplicates:
            state.append(f"duplicate legacy entries: {len(duplicates)}")
        print(f"{hook['id']}: {', '.join(state)}")
        if drift:
            print(f"target differs from manifest source: {drift['target']}")
            print(f"manifest source: {drift['source']}")
        for entry in sorted(duplicates, key=lambda item: (item["event"], item["command"])):
            print_legacy_duplicate(entry)


def install(manifest: dict, selected_hooks: list[dict], target_dir: Path, dry_run: bool, adopt_existing: bool = False) -> None:
    config = read_hooks_json()
    selected_ids = {hook["id"] for hook in selected_hooks}
    managed_targets = managed_hook_targets(config, selected_hooks, target_dir)
    adoptions, duplicate_entries = install_plan(config, selected_hooks, target_dir, managed_targets, adopt_existing)
    managed_drifts = managed_source_target_drifts(selected_hooks, target_dir, managed_targets)

    if dry_run:
        print_dry_run_install(selected_hooks, target_dir, adoptions, duplicate_entries, managed_drifts, adopt_existing)
        return

    target_backups = []
    hooks_json_backup = None
    if adoptions:
        for adoption in adoptions:
            target_backups.append((adoption["hook"]["id"], backup_file(adoption["target"])))
        hooks_json_backup = backup_hooks_json(HOOKS_JSON_PATH)

    remove_managed_entries(config, selected_ids, duplicate_entries if adopt_existing else None)
    add_managed_entries(config, selected_hooks, target_dir)

    adopted_targets = {(adoption["hook"]["id"], adoption["target"]) for adoption in adoptions}
    install_hook_files(selected_hooks, target_dir, managed_targets | adopted_targets)
    backup_path = write_hooks_json(config, create_backup=not bool(adoptions))
    for hook_id, backup_path_item in target_backups:
        print(f"backup target {hook_id}: {backup_path_item}")
    if hooks_json_backup:
        print(f"backup: {hooks_json_backup}")
    elif backup_path:
        print(f"backup: {backup_path}")
    for adoption in adoptions:
        hook = adoption["hook"]
        print(f"adopted existing {hook['id']} -> {adoption['target']}")
    for hook in selected_hooks:
        print(f"installed {hook['id']} -> {target_dir / hook['target']}")


def disable(selected_hooks: list[dict], dry_run: bool) -> None:
    config = read_hooks_json()
    selected_ids = {hook["id"] for hook in selected_hooks}
    remove_managed_entries(config, selected_ids)

    for hook in selected_hooks:
        prefix = "would disable" if dry_run else "disabled"
        print(f"{prefix} {hook['id']}")

    if dry_run:
        return
    backup_path = write_hooks_json(config)
    if backup_path:
        print(f"backup: {backup_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install SmartCodex-managed Codex hooks.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List hooks defined by the manifest.")
    list_parser.add_argument("--manifest", default=str(MANIFEST_PATH), help="Manifest path.")

    status_parser = subparsers.add_parser("status", help="Show install and enablement state.")
    status_parser.add_argument("hooks", nargs="*", help="Hook ids to check. Defaults to all hooks.")
    status_parser.add_argument("--target-dir", help="Hook script install directory.")
    status_parser.add_argument("--manifest", default=str(MANIFEST_PATH), help="Manifest path.")
    status_parser.add_argument("--dry-run", action="store_true", help="Accepted for validation; status never writes.")

    install_parser = subparsers.add_parser("install", help="Install and enable selected hooks.")
    install_parser.add_argument("hooks", nargs="*", help="Hook ids to install. Defaults to all hooks.")
    install_parser.add_argument("--target-dir", help="Hook script install directory.")
    install_parser.add_argument("--manifest", default=str(MANIFEST_PATH), help="Manifest path.")
    install_parser.add_argument(
        "--adopt-existing",
        action="store_true",
        help="Adopt a recognized legacy SmartCodex hook target before replacing it.",
    )

    disable_parser = subparsers.add_parser("disable", help="Disable selected managed hooks in hooks.json.")
    disable_parser.add_argument("hooks", nargs="*", help="Hook ids to disable. Defaults to all hooks.")
    disable_parser.add_argument("--manifest", default=str(MANIFEST_PATH), help="Manifest path.")

    dry_run_parser = subparsers.add_parser("dry-run", help="Preview an install or disable operation.")
    dry_run_parser.add_argument("action", choices=("install", "disable"), help="Operation to preview.")
    dry_run_parser.add_argument("hooks", nargs="*", help="Hook ids to operate on. Defaults to all hooks.")
    dry_run_parser.add_argument("--target-dir", help="Hook script install directory.")
    dry_run_parser.add_argument("--manifest", default=str(MANIFEST_PATH), help="Manifest path.")
    dry_run_parser.add_argument(
        "--adopt-existing",
        action="store_true",
        help="Preview adoption of a recognized legacy SmartCodex hook target.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(Path(args.manifest))

        if args.command == "list":
            print_hook_list(manifest)
            return 0

        if args.command == "dry-run":
            selected_hooks = select_hooks(manifest, args.hooks)
            target_dir = expand_target_dir(args.target_dir, manifest)
            if args.action == "install":
                install(manifest, selected_hooks, target_dir, dry_run=True, adopt_existing=args.adopt_existing)
            else:
                disable(selected_hooks, dry_run=True)
            return 0

        selected_hooks = select_hooks(manifest, args.hooks)
        target_dir = expand_target_dir(getattr(args, "target_dir", None), manifest)

        if args.command == "status":
            print_status(manifest, selected_hooks, target_dir)
            return 0
        if args.command == "install":
            install(manifest, selected_hooks, target_dir, dry_run=False, adopt_existing=args.adopt_existing)
            return 0
        if args.command == "disable":
            disable(selected_hooks, dry_run=False)
            return 0

        parser.error(f"unsupported command: {args.command}")
        return 2
    except (OSError, json.JSONDecodeError, HookUpdateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
