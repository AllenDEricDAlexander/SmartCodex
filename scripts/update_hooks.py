#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import filecmp
import json
import os
import plistlib
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "hooks" / "hooks.manifest.json"
HOOKS_JSON_PATH = Path.home() / ".codex" / "hooks.json"
MANAGED_MARKER = "SMARTCODEX_MANAGED_HOOK="
CODEX_RESOURCES_ENV = "SMARTCODEX_CODEX_RESOURCES_DIR"
CODEX_APP_RESOURCES = Path("/Applications/Codex.app/Contents/Resources")
CODEX_ICON_NAMES = ("icon.icns", "electron.icns")
MACOS_HELPER_APP_NAME = "CodexNotify.app"
MACOS_HELPER_APP_DISPLAY_NAME = "Codex Notify"
MACOS_HELPER_APP_BUNDLE_ID = "com.smartcodex.notify"
MACOS_HELPER_EXECUTABLE_NAME = "CodexNotify"
LSREGISTER_PATH = Path(
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
)


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
    return template.format(id=hook["id"], target_path=str(target_path), target_dir=str(target_dir))


def hook_helpers(hook: dict) -> list[dict]:
    helpers = hook.get("helpers", [])
    if helpers is None:
        return []
    if not isinstance(helpers, list):
        raise HookUpdateError(f"manifest hook helpers must be a list: {hook['id']}")
    return helpers


def helper_source_dir(helper: dict) -> Path:
    return ROOT / "hooks" / helper["source"]


def helper_target_dir(helper: dict, target_dir: Path) -> Path:
    return target_dir / helper["target"]


def helper_source_files(helper: dict) -> list[Path]:
    source_dir = helper_source_dir(helper)
    if not source_dir.exists():
        raise HookUpdateError(f"helper source does not exist: {source_dir}")
    if not source_dir.is_dir():
        raise HookUpdateError(f"helper source is not a directory: {source_dir}")
    return sorted(path for path in source_dir.rglob("*") if path.is_file())


def helper_executable_paths(helper: dict) -> set[Path]:
    return {Path(value) for value in helper.get("executables", [])}


def helper_app_source_paths(helper: dict) -> list[Path]:
    return [Path(value) for value in helper.get("app_sources", [])]


def helper_native_source_paths(helper: dict) -> list[Path]:
    return [Path(value) for value in helper.get("native_sources", [])]


def codex_resource_dirs() -> list[Path]:
    override = os.environ.get(CODEX_RESOURCES_ENV)
    if override:
        return [Path(os.path.expanduser(override))]
    return [CODEX_APP_RESOURCES]


def icon_name_with_suffix(value: str) -> str:
    icon_name = value.strip()
    if not icon_name:
        return ""
    if Path(icon_name).suffix:
        return icon_name
    return icon_name + ".icns"


def codex_icon_names(resource_dir: Path) -> list[str]:
    names = []
    info_path = resource_dir.parent / "Info.plist"
    if info_path.exists():
        try:
            with info_path.open("rb") as f:
                plist = plistlib.load(f)
            icon_name = icon_name_with_suffix(str(plist.get("CFBundleIconFile", "")))
            if icon_name:
                names.append(icon_name)
        except Exception:
            pass

    names.extend(CODEX_ICON_NAMES)
    deduped = []
    for name in names:
        if name and name not in deduped:
            deduped.append(name)
    return deduped


def codex_icon_source() -> Path | None:
    for resource_dir in codex_resource_dirs():
        for icon_name in codex_icon_names(resource_dir):
            icon_path = resource_dir / icon_name
            if icon_path.exists():
                return icon_path
    return None


def configure_macos_helper_app(app_dir: Path, executable_name: str) -> None:
    contents_dir = app_dir / "Contents"
    plist_path = contents_dir / "Info.plist"
    plist = {}
    if plist_path.exists():
        try:
            with plist_path.open("rb") as f:
                plist = plistlib.load(f)
        except Exception:
            plist = {}

    plist.update(
        {
            "CFBundleDisplayName": MACOS_HELPER_APP_DISPLAY_NAME,
            "CFBundleName": MACOS_HELPER_APP_DISPLAY_NAME,
            "CFBundleExecutable": executable_name,
            "CFBundleIdentifier": MACOS_HELPER_APP_BUNDLE_ID,
            "CFBundleIconFile": "icon",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "1.0",
            "CFBundleVersion": datetime.now().strftime("%Y%m%d%H%M%S"),
        }
    )
    contents_dir.mkdir(parents=True, exist_ok=True)
    with plist_path.open("wb") as f:
        plistlib.dump(plist, f)


def codesign_macos_helper_app(app_dir: Path) -> None:
    codesign = shutil.which("codesign")
    if not codesign:
        return
    subprocess.run(
        [codesign, "--force", "--deep", "--sign", "-", str(app_dir)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def compile_macos_native_helper_app(helper: dict, helper_dir: Path) -> bool:
    native_sources = helper_native_source_paths(helper)
    if not native_sources:
        return False

    source = next((helper_dir / path for path in native_sources if (helper_dir / path).exists()), None)
    if source is None:
        raise HookUpdateError(f"helper native source does not exist: {native_sources[0]}")

    compiler = shutil.which("swiftc")
    if not compiler:
        print("warning: swiftc not found; helper will use osascript fallback")
        return False

    app_dir = helper_dir / MACOS_HELPER_APP_NAME
    if app_dir.exists():
        shutil.rmtree(app_dir)

    executable = app_dir / "Contents" / "MacOS" / MACOS_HELPER_EXECUTABLE_NAME
    executable.parent.mkdir(parents=True, exist_ok=True)
    module_cache = Path(tempfile.gettempdir()) / "smartcodex-swift-module-cache"
    module_cache.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            compiler,
            str(source),
            "-o",
            str(executable),
            "-framework",
            "UserNotifications",
            "-module-cache-path",
            str(module_cache),
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            detail = ": " + detail.splitlines()[-1]
        print("warning: failed to compile native Codex notification helper app" + detail)
        return False

    mode = executable.stat().st_mode
    executable.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    configure_macos_helper_app(app_dir, MACOS_HELPER_EXECUTABLE_NAME)
    codesign_macos_helper_app(app_dir)
    return True


def compile_macos_applescript_helper_app(helper: dict, helper_dir: Path) -> bool:
    app_sources = helper_app_source_paths(helper)
    if not app_sources:
        return False

    source = next((helper_dir / path for path in app_sources if (helper_dir / path).exists()), None)
    if source is None:
        raise HookUpdateError(f"helper app source does not exist: {app_sources[0]}")

    compiler = shutil.which("osacompile")
    if not compiler:
        print("warning: osacompile not found; helper will use osascript fallback")
        return False

    app_dir = helper_dir / MACOS_HELPER_APP_NAME
    if app_dir.exists():
        shutil.rmtree(app_dir)

    result = subprocess.run(
        [compiler, "-o", str(app_dir), str(source)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            detail = ": " + detail.splitlines()[-1]
        print("warning: failed to compile Codex notification helper app" + detail)
        return False

    configure_macos_helper_app(app_dir, "applet")
    codesign_macos_helper_app(app_dir)
    return True


def compile_macos_helper_app(helper: dict, helper_dir: Path) -> bool:
    if compile_macos_native_helper_app(helper, helper_dir):
        return True
    return compile_macos_applescript_helper_app(helper, helper_dir)


def install_helper_icon(helper_dir: Path) -> bool:
    resources_dir = helper_dir / MACOS_HELPER_APP_NAME / "Contents" / "Resources"
    icon_target = resources_dir / "icon.icns"
    icon_source = codex_icon_source()
    if icon_source:
        resources_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(icon_source, icon_target)
        return True
    if icon_target.exists():
        icon_target.unlink()
    print("warning: Codex icon not found; helper will use osascript fallback")
    return False


def register_macos_helper_app(helper_dir: Path) -> None:
    app_dir = helper_dir / MACOS_HELPER_APP_NAME
    if not app_dir.exists() or not LSREGISTER_PATH.exists():
        return
    subprocess.run(
        [str(LSREGISTER_PATH), "-f", str(app_dir)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def copy_helper_files(helper: dict, target_dir: Path) -> None:
    source_dir = helper_source_dir(helper)
    target_helper_dir = helper_target_dir(helper, target_dir)
    executable_paths = helper_executable_paths(helper)
    for source in helper_source_files(helper):
        relative = source.relative_to(source_dir)
        target = target_helper_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if relative in executable_paths:
            mode = target.stat().st_mode
            target.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if compile_macos_helper_app(helper, target_helper_dir):
        install_helper_icon(target_helper_dir)
        register_macos_helper_app(target_helper_dir)


def source_target_drift(hook: dict, target_dir: Path) -> dict | None:
    source = ROOT / "hooks" / hook["source"]
    target = target_dir / hook["target"]
    if not source.exists() or not target.exists():
        return None
    if filecmp.cmp(source, target, shallow=False):
        return None
    return {"hook": hook, "source": source, "target": target}


def helper_source_target_drifts(selected_hooks: list[dict], target_dir: Path) -> list[dict]:
    drifts = []
    for hook in selected_hooks:
        for helper in hook_helpers(hook):
            source_dir = helper_source_dir(helper)
            target_helper_dir = helper_target_dir(helper, target_dir)
            for source in helper_source_files(helper):
                target = target_helper_dir / source.relative_to(source_dir)
                if not target.exists() or not filecmp.cmp(source, target, shallow=False):
                    drifts.append({"hook": hook, "helper": helper, "source": source, "target": target})
    return drifts


def managed_helper_source_target_drifts(
    selected_hooks: list[dict],
    target_dir: Path,
    managed_targets: set[tuple[str, Path]],
) -> list[dict]:
    drifts = []
    for hook in selected_hooks:
        if (hook["id"], target_dir / hook["target"]) not in managed_targets:
            continue
        drifts.extend(helper_source_target_drifts([hook], target_dir))
    return drifts


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
        for helper in hook_helpers(hook):
            helper_source_files(helper)
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
        for helper in hook_helpers(hook):
            copy_helper_files(helper, target_dir)


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
        if "helper" in drift:
            print(f"would replace managed helper target: {drift['target']}")
            print(f"helper source differs from installed target: {drift['source']}")
        else:
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
    managed_targets = managed_hook_targets(config, selected_hooks, target_dir)
    for hook in selected_hooks:
        installed = (target_dir / hook["target"]).exists()
        enabled = hook_is_enabled(config, hook["id"])
        drift = source_target_drift(hook, target_dir)
        helper_drifts = helper_source_target_drifts([hook], target_dir) if (hook["id"], target_dir / hook["target"]) in managed_targets else []
        state = []
        state.append("installed" if installed else "not installed")
        state.append("enabled" if enabled else "disabled")
        if drift:
            state.append("source drift")
        if helper_drifts:
            state.append("helper source drift")
        duplicates = [entry for entry in duplicate_entries if entry["hook_id"] == hook["id"]]
        if duplicates:
            state.append(f"duplicate legacy entries: {len(duplicates)}")
        print(f"{hook['id']}: {', '.join(state)}")
        if drift:
            print(f"target differs from manifest source: {drift['target']}")
            print(f"manifest source: {drift['source']}")
        for helper_drift in helper_drifts:
            print(f"helper target differs from manifest source: {helper_drift['target']}")
            print(f"helper manifest source: {helper_drift['source']}")
        for entry in sorted(duplicates, key=lambda item: (item["event"], item["command"])):
            print_legacy_duplicate(entry)


def install(manifest: dict, selected_hooks: list[dict], target_dir: Path, dry_run: bool, adopt_existing: bool = False) -> None:
    config = read_hooks_json()
    selected_ids = {hook["id"] for hook in selected_hooks}
    managed_targets = managed_hook_targets(config, selected_hooks, target_dir)
    adoptions, duplicate_entries = install_plan(config, selected_hooks, target_dir, managed_targets, adopt_existing)
    managed_drifts = managed_source_target_drifts(selected_hooks, target_dir, managed_targets)
    managed_drifts.extend(managed_helper_source_target_drifts(selected_hooks, target_dir, managed_targets))

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
