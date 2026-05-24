import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "update_hooks.py"
MANIFEST_PATH = ROOT / "hooks" / "hooks.manifest.json"


class UpdateHooksScriptTest(unittest.TestCase):
    def run_script(self, home: Path, *args: str, extra_env=None):
        env = os.environ.copy()
        env["HOME"] = str(home)
        if extra_env:
            env.update(extra_env)
        env.pop("PYTHONHOME", None)
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), *args],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def load_hooks_json(self, home: Path) -> dict:
        with (home / ".codex" / "hooks.json").open(encoding="utf-8") as f:
            return json.load(f)

    def managed_commands(self, config: dict) -> list[str]:
        commands = []
        for groups in config.get("hooks", {}).values():
            for group in groups:
                for hook in group.get("hooks", []):
                    command = hook.get("command", "")
                    if "SMARTCODEX_MANAGED_HOOK=" in command:
                        commands.append(command)
        return commands

    def codex_resources(self, tmp: str, icon_name="icon.icns") -> Path:
        resources = Path(tmp) / "Codex.app" / "Contents" / "Resources"
        resources.mkdir(parents=True)
        with (resources.parent / "Info.plist").open("wb") as f:
            plistlib.dump({"CFBundleIconFile": "electron.icns"}, f)
        if icon_name:
            (resources / icon_name).write_bytes(b"fake icns")
        return resources

    def fake_osacompile(self, tmp: str) -> tuple[Path, Path]:
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir(exist_ok=True)
        log_path = Path(tmp) / "osacompile.log"
        fake = bin_dir / "osacompile"
        fake.write_text(
            "#!/bin/sh\n"
            "out=''\n"
            "src=''\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  if [ \"$1\" = '-o' ]; then\n"
            "    shift\n"
            "    out=\"$1\"\n"
            "  else\n"
            "    src=\"$1\"\n"
            "  fi\n"
            "  shift\n"
            "done\n"
            "mkdir -p \"$out/Contents/MacOS\" \"$out/Contents/Resources\"\n"
            "printf '%s -> %s\\n' \"$src\" \"$out\" > " + str(log_path) + "\n"
            "printf '#!/bin/sh\\n' > \"$out/Contents/MacOS/applet\"\n"
            "chmod +x \"$out/Contents/MacOS/applet\"\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        return bin_dir, log_path

    def fake_swiftc(self, tmp: str) -> tuple[Path, Path]:
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir(exist_ok=True)
        log_path = Path(tmp) / "swiftc.log"
        fake = bin_dir / "swiftc"
        fake.write_text(
            "#!/bin/sh\n"
            "out=''\n"
            "src=''\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  if [ \"$1\" = '-o' ]; then\n"
            "    shift\n"
            "    out=\"$1\"\n"
            "  else\n"
            "    case \"$1\" in\n"
            "      *.swift) src=\"$1\" ;;\n"
            "    esac\n"
            "  fi\n"
            "  shift\n"
            "done\n"
            "mkdir -p \"$(dirname \"$out\")\"\n"
            "printf '%s -> %s\\n' \"$src\" \"$out\" > " + str(log_path) + "\n"
            "printf '#!/bin/sh\\n' > \"$out\"\n"
            "chmod +x \"$out\"\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        return bin_dir, log_path

    def test_manifest_is_available_for_cli_operations(self):
        self.assertTrue(MANIFEST_PATH.exists())

        with MANIFEST_PATH.open(encoding="utf-8") as f:
            manifest = json.load(f)

        self.assertEqual(1, manifest["schema_version"])
        self.assertEqual("~/.codex/hooks", manifest["default_target_dir"])
        self.assertIn("notify", {hook["id"] for hook in manifest["hooks"]})

    def test_notify_manifest_registers_required_documented_events(self):
        with MANIFEST_PATH.open(encoding="utf-8") as f:
            manifest = json.load(f)

        notify_hook = next(hook for hook in manifest["hooks"] if hook["id"] == "notify")

        self.assertIn("SessionStart", notify_hook["events"])
        self.assertIn("UserPromptSubmit", notify_hook["events"])
        self.assertIn("SubagentStart", notify_hook["events"])
        self.assertIn("SubagentStop", notify_hook["events"])
        self.assertIn("Stop", notify_hook["events"])
        self.assertIn("PermissionRequest", notify_hook["events"])

    def test_notify_manifest_exposes_stable_macos_helper_interface(self):
        with MANIFEST_PATH.open(encoding="utf-8") as f:
            manifest = json.load(f)

        notify_hook = next(hook for hook in manifest["hooks"] if hook["id"] == "notify")
        helper = next(helper for helper in notify_hook["helpers"] if helper["id"] == "macos_notify")

        self.assertEqual("notify/macos_helper", helper["source"])
        self.assertEqual("notify_macos_helper", helper["target"])
        self.assertEqual("codex_macos_notify.py", helper["entrypoint"])
        self.assertEqual("SMARTCODEX_NOTIFY_HELPER", helper["runtime_env"])
        self.assertIn("CodexNotifyApp.swift", helper["native_sources"])
        self.assertIn("codex_notify.applescript", helper["app_sources"])
        self.assertIn("SMARTCODEX_NOTIFY_HELPER=", notify_hook["command_template"])

    def test_notify_manifest_does_not_depend_on_unsupported_agent_stop(self):
        with MANIFEST_PATH.open(encoding="utf-8") as f:
            manifest = json.load(f)

        notify_hook = next(hook for hook in manifest["hooks"] if hook["id"] == "notify")

        self.assertNotIn("AgentStop", notify_hook["events"])

    def test_list_prints_manifest_hook_ids_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            result = self.run_script(home, "list")

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("notify", result.stdout)
            self.assertFalse((home / ".codex" / "hooks.json").exists())

    def test_list_accepts_manifest_option_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            result = self.run_script(home, "list", "--manifest", str(MANIFEST_PATH))

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("notify", result.stdout)
            self.assertFalse((home / ".codex" / "hooks.json").exists())

    def test_install_selective_hook_preserves_user_hooks_and_writes_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hooks_json = home / ".codex" / "hooks.json"
            hooks_json.parent.mkdir(parents=True)
            original_config = {
                "description": "user config",
                "hooks": {
                    "PermissionRequest": [
                        {
                            "matcher": "functions.exec_command",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 /user/permission.py",
                                    "timeout": 3,
                                }
                            ],
                        }
                    ],
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 /user/stop.py",
                                }
                            ]
                        }
                    ],
                },
            }
            hooks_json.write_text(json.dumps(original_config, indent=2), encoding="utf-8")

            result = self.run_script(home, "install", "notify")

            self.assertEqual(0, result.returncode, result.stderr)
            updated = self.load_hooks_json(home)
            managed_commands = self.managed_commands(updated)
            self.assertEqual(6, len(managed_commands))
            self.assertTrue(all("SMARTCODEX_MANAGED_HOOK=notify" in command for command in managed_commands))
            self.assertIn("python3 /user/permission.py", json.dumps(updated))
            self.assertIn("python3 /user/stop.py", json.dumps(updated))
            self.assertTrue((home / ".codex" / "hooks" / "codex_notify.py").exists())

            backups = sorted(hooks_json.parent.glob("hooks.json.smartcodex-backup-*"))
            self.assertEqual(1, len(backups))
            with backups[0].open(encoding="utf-8") as f:
                self.assertEqual(original_config, json.load(f))

    def test_install_copies_macos_helper_with_codex_icon_and_runtime_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            resources = self.codex_resources(tmp, "icon.icns")
            bin_dir, compile_log = self.fake_swiftc(tmp)

            result = self.run_script(
                home,
                "install",
                "notify",
                extra_env={
                    "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
                    "SMARTCODEX_CODEX_RESOURCES_DIR": str(resources),
                },
            )

            self.assertEqual(0, result.returncode, result.stderr)
            helper_dir = home / ".codex" / "hooks" / "notify_macos_helper"
            helper_script = helper_dir / "codex_macos_notify.py"
            app_script = helper_dir / "CodexNotify.app" / "Contents" / "MacOS" / "CodexNotify"
            app_icon = helper_dir / "CodexNotify.app" / "Contents" / "Resources" / "icon.icns"
            app_plist = helper_dir / "CodexNotify.app" / "Contents" / "Info.plist"
            self.assertTrue(helper_script.exists())
            self.assertTrue(os.access(helper_script, os.X_OK))
            self.assertTrue(app_script.exists())
            self.assertTrue(os.access(app_script, os.X_OK))
            self.assertEqual(b"fake icns", app_icon.read_bytes())
            with app_plist.open("rb") as f:
                plist = plistlib.load(f)
            self.assertEqual("Codex Notify", plist["CFBundleDisplayName"])
            self.assertEqual("com.smartcodex.notify", plist["CFBundleIdentifier"])
            self.assertEqual("CodexNotify", plist["CFBundleExecutable"])
            self.assertEqual("icon", plist["CFBundleIconFile"])
            self.assertIn("CodexNotifyApp.swift", compile_log.read_text(encoding="utf-8"))

            updated = self.load_hooks_json(home)
            commands = self.managed_commands(updated)
            self.assertTrue(all(f'SMARTCODEX_NOTIFY_HELPER="{helper_script.resolve()}"' in command for command in commands))
            self.assertNotIn("terminal-notifier", json.dumps(updated))
            self.assertNotIn("terminal-notifier", helper_script.read_text(encoding="utf-8"))

    def test_install_prefers_codex_bundle_icon_name_from_info_plist(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            resources = self.codex_resources(tmp, None)
            (resources / "icon.icns").write_bytes(b"generic icon")
            (resources / "electron.icns").write_bytes(b"codex app icon")
            bin_dir, _ = self.fake_swiftc(tmp)

            result = self.run_script(
                home,
                "install",
                "notify",
                extra_env={
                    "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
                    "SMARTCODEX_CODEX_RESOURCES_DIR": str(resources),
                },
            )

            self.assertEqual(0, result.returncode, result.stderr)
            app_icon = home / ".codex" / "hooks" / "notify_macos_helper" / "CodexNotify.app" / "Contents" / "Resources" / "icon.icns"
            self.assertEqual(b"codex app icon", app_icon.read_bytes())

    def test_macos_helper_launches_installed_app_when_icon_is_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            resources = self.codex_resources(tmp, "icon.icns")
            install_result = self.run_script(
                home,
                "install",
                "notify",
                extra_env={"SMARTCODEX_CODEX_RESOURCES_DIR": str(resources)},
            )
            self.assertEqual(0, install_result.returncode, install_result.stderr)

            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            open_log = Path(tmp) / "open.log"
            fake_open = bin_dir / "open"
            fake_open.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$@\" > {open_log!s}\n"
                "while [ \"$#\" -gt 0 ]; do\n"
                "  if [ \"$1\" = \"--status-file\" ]; then\n"
                "    shift\n"
                "    printf 'ok' > \"$1\"\n"
                "    exit 0\n"
                "  fi\n"
                "  shift\n"
                "done\n",
                encoding="utf-8",
            )
            fake_open.chmod(0o755)
            helper_script = home / ".codex" / "hooks" / "notify_macos_helper" / "codex_macos_notify.py"

            env = os.environ.copy()
            env["PATH"] = str(bin_dir)
            env.pop("PYTHONHOME", None)
            result = subprocess.run(
                [sys.executable, str(helper_script), "--title", "T", "--message", "M"],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            open_args = open_log.read_text(encoding="utf-8")
            self.assertIn("-g", open_args)
            self.assertNotIn("-W", open_args)
            self.assertIn("--status-file", open_args)
            self.assertNotIn("-j", open_args)
            self.assertIn(str(helper_script.parent / "CodexNotify.app"), open_args)

    def test_install_keeps_helper_available_when_codex_icon_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            resources = self.codex_resources(tmp, None)

            result = self.run_script(
                home,
                "install",
                "notify",
                extra_env={"SMARTCODEX_CODEX_RESOURCES_DIR": str(resources)},
            )

            self.assertEqual(0, result.returncode, result.stderr)
            helper_dir = home / ".codex" / "hooks" / "notify_macos_helper"
            helper_script = helper_dir / "codex_macos_notify.py"
            app_icon = helper_dir / "CodexNotify.app" / "Contents" / "Resources" / "icon.icns"
            self.assertTrue(helper_script.exists())
            self.assertFalse(app_icon.exists())
            self.assertIn("Codex icon not found; helper will use osascript fallback", result.stdout)

    def test_install_refuses_to_clobber_unmanaged_target_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hooks_json = home / ".codex" / "hooks.json"
            target_script = home / ".codex" / "hooks" / "codex_notify.py"
            hooks_json.parent.mkdir(parents=True)
            target_script.parent.mkdir(parents=True)
            target_script.write_text("# user owned notify hook\n", encoding="utf-8")
            original_config = {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f'python3 "{target_script}"',
                                }
                            ]
                        }
                    ]
                }
            }
            hooks_json.write_text(json.dumps(original_config, indent=2), encoding="utf-8")

            result = self.run_script(home, "install", "notify")

            self.assertNotEqual(0, result.returncode)
            self.assertIn("refusing to overwrite unmanaged hook target", result.stderr)
            self.assertEqual("# user owned notify hook\n", target_script.read_text(encoding="utf-8"))
            self.assertEqual(original_config, self.load_hooks_json(home))
            self.assertFalse(list(hooks_json.parent.glob("hooks.json.smartcodex-backup-*")))

    def test_dry_run_detects_unmanaged_non_identical_target_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hooks_json = home / ".codex" / "hooks.json"
            target_script = home / ".codex" / "hooks" / "codex_notify.py"
            hooks_json.parent.mkdir(parents=True)
            target_script.parent.mkdir(parents=True)
            target_script.write_text("# user owned notify hook\n", encoding="utf-8")
            original_config = {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f'python3 "{target_script}"',
                                }
                            ]
                        }
                    ]
                }
            }
            hooks_json.write_text(json.dumps(original_config, indent=2), encoding="utf-8")

            result = self.run_script(home, "dry-run", "install", "notify")

            self.assertNotEqual(0, result.returncode)
            self.assertIn("refusing to overwrite unmanaged hook target", result.stderr)
            self.assertIn("--adopt-existing", result.stderr)
            self.assertNotIn("would install notify", result.stdout)
            self.assertEqual("# user owned notify hook\n", target_script.read_text(encoding="utf-8"))
            self.assertEqual(original_config, self.load_hooks_json(home))
            self.assertFalse(list(hooks_json.parent.glob("hooks.json.smartcodex-backup-*")))

    def test_install_refusal_does_not_print_installed_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            target_script = home / ".codex" / "hooks" / "codex_notify.py"
            target_script.parent.mkdir(parents=True)
            target_script.write_text("# user owned notify hook\n", encoding="utf-8")

            result = self.run_script(home, "install", "notify")

            self.assertNotEqual(0, result.returncode)
            self.assertIn("refusing to overwrite unmanaged hook target", result.stderr)
            self.assertNotIn("installed notify", result.stdout)

    def test_install_success_message_waits_for_copy_and_write_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target_dir = Path(tmp) / "not-a-directory"
            target_dir.write_text("blocks mkdir\n", encoding="utf-8")

            result = self.run_script(home, "install", "notify", "--target-dir", str(target_dir))

            self.assertNotEqual(0, result.returncode)
            self.assertNotIn("installed notify", result.stdout)
            self.assertFalse((home / ".codex" / "hooks.json").exists())

    def test_dry_run_adopt_existing_reports_planned_migration_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hooks_json = home / ".codex" / "hooks.json"
            target_script = home / ".codex" / "hooks" / "codex_notify.py"
            hooks_json.parent.mkdir(parents=True)
            target_script.parent.mkdir(parents=True)
            target_script.write_text("# legacy notify hook\n", encoding="utf-8")
            original_config = {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python3 "$HOME/.codex/hooks/codex_notify.py"',
                                }
                            ]
                        }
                    ]
                }
            }
            original_text = json.dumps(original_config, indent=2)
            hooks_json.write_text(original_text, encoding="utf-8")

            result = self.run_script(home, "dry-run", "install", "notify", "--adopt-existing")

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("would adopt existing notify", result.stdout)
            self.assertIn(
                'would remove duplicate legacy notify hook for Stop: python3 "$HOME/.codex/hooks/codex_notify.py"',
                result.stdout,
            )
            self.assertIn("would backup target", result.stdout)
            self.assertIn("would backup hooks.json", result.stdout)
            self.assertIn("would replace target", result.stdout)
            self.assertIn("would update hooks.json", result.stdout)
            self.assertEqual("# legacy notify hook\n", target_script.read_text(encoding="utf-8"))
            self.assertEqual(original_text, hooks_json.read_text(encoding="utf-8"))
            self.assertFalse(list(hooks_json.parent.glob("hooks.json.smartcodex-backup-*")))
            self.assertFalse(list(target_script.parent.glob("codex_notify.py.smartcodex-backup-*")))

    def test_status_reports_duplicate_old_unmarked_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hooks_json = home / ".codex" / "hooks.json"
            hooks_json.parent.mkdir(parents=True)
            original_config = {
                "hooks": {
                    "SubagentStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python3 "$HOME/.codex/hooks/codex_notify.py"',
                                }
                            ]
                        }
                    ],
                    "PermissionRequest": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python3 "$HOME/.codex/hooks/codex_notify.py"',
                                }
                            ]
                        }
                    ],
                }
            }
            hooks_json.write_text(json.dumps(original_config, indent=2), encoding="utf-8")

            result = self.run_script(home, "status", "--dry-run")

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("notify: not installed, disabled, duplicate legacy entries: 2", result.stdout)
            self.assertIn(
                'duplicate legacy notify hook for PermissionRequest: python3 "$HOME/.codex/hooks/codex_notify.py"',
                result.stdout,
            )
            self.assertIn(
                'duplicate legacy notify hook for SubagentStart: python3 "$HOME/.codex/hooks/codex_notify.py"',
                result.stdout,
            )
            self.assertEqual(original_config, self.load_hooks_json(home))

    def test_dry_run_adopt_existing_reports_duplicate_cleanup_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hooks_json = home / ".codex" / "hooks.json"
            hooks_json.parent.mkdir(parents=True)
            original_config = {
                "hooks": {
                    "SubagentStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python3 "$HOME/.codex/hooks/codex_notify.py"',
                                },
                                {
                                    "type": "command",
                                    "command": "python3 /user/subagent.py",
                                },
                            ]
                        }
                    ]
                }
            }
            original_text = json.dumps(original_config, indent=2)
            hooks_json.write_text(original_text, encoding="utf-8")

            result = self.run_script(home, "dry-run", "install", "notify", "--adopt-existing")

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(
                'would remove duplicate legacy notify hook for SubagentStart: python3 "$HOME/.codex/hooks/codex_notify.py"',
                result.stdout,
            )
            self.assertIn("would backup hooks.json", result.stdout)
            self.assertIn("would update hooks.json", result.stdout)
            self.assertEqual(original_text, hooks_json.read_text(encoding="utf-8"))
            self.assertFalse(list(hooks_json.parent.glob("hooks.json.smartcodex-backup-*")))

    def test_dry_run_install_reports_duplicate_legacy_entries_without_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hooks_json = home / ".codex" / "hooks.json"
            hooks_json.parent.mkdir(parents=True)
            original_config = {
                "hooks": {
                    "PermissionRequest": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python3 "$HOME/.codex/hooks/codex_notify.py"',
                                }
                            ]
                        }
                    ]
                }
            }
            original_text = json.dumps(original_config, indent=2)
            hooks_json.write_text(original_text, encoding="utf-8")

            result = self.run_script(home, "dry-run", "install", "notify")

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(
                'duplicate legacy notify hook for PermissionRequest: python3 "$HOME/.codex/hooks/codex_notify.py"',
                result.stdout,
            )
            self.assertIn("--adopt-existing", result.stdout)
            self.assertIn("would install notify", result.stdout)
            self.assertEqual(original_text, hooks_json.read_text(encoding="utf-8"))
            self.assertFalse(list(hooks_json.parent.glob("hooks.json.smartcodex-backup-*")))

    def test_adopt_existing_only_removes_recognized_legacy_notify_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hooks_json = home / ".codex" / "hooks.json"
            hooks_json.parent.mkdir(parents=True)
            original_config = {
                "hooks": {
                    "SubagentStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python3 "$HOME/.codex/hooks/codex_notify.py"',
                                },
                                {
                                    "type": "command",
                                    "command": "python3 /user/codex_notify.py",
                                },
                            ]
                        }
                    ],
                    "CustomEvent": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python3 "$HOME/.codex/hooks/codex_notify.py"',
                                }
                            ]
                        }
                    ],
                }
            }
            hooks_json.write_text(json.dumps(original_config, indent=2), encoding="utf-8")

            result = self.run_script(home, "install", "notify", "--adopt-existing")

            self.assertEqual(0, result.returncode, result.stderr)
            updated = self.load_hooks_json(home)
            updated_text = json.dumps(updated)
            self.assertIn("python3 /user/codex_notify.py", updated_text)
            self.assertIn("CustomEvent", updated["hooks"])
            self.assertEqual(
                'python3 "$HOME/.codex/hooks/codex_notify.py"',
                updated["hooks"]["CustomEvent"][0]["hooks"][0]["command"],
            )
            self.assertNotIn(
                'python3 "$HOME/.codex/hooks/codex_notify.py"',
                json.dumps(updated["hooks"]["SubagentStart"]),
            )

    def test_install_with_adoption_does_not_leave_duplicate_notify_hooks_per_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hooks_json = home / ".codex" / "hooks.json"
            hooks_json.parent.mkdir(parents=True)
            original_config = {
                "hooks": {
                    "SubagentStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python3 "$HOME/.codex/hooks/codex_notify.py"',
                                },
                                {
                                    "type": "command",
                                    "command": 'SMARTCODEX_MANAGED_HOOK=notify python3 "$HOME/.codex/hooks/codex_notify.py"',
                                },
                            ]
                        }
                    ]
                }
            }
            hooks_json.write_text(json.dumps(original_config, indent=2), encoding="utf-8")

            result = self.run_script(home, "install", "notify", "--adopt-existing")

            self.assertEqual(0, result.returncode, result.stderr)
            updated = self.load_hooks_json(home)
            notify_command_counts = {}
            for event, groups in updated["hooks"].items():
                count = 0
                for group in groups:
                    for hook in group.get("hooks", []):
                        command = hook.get("command", "")
                        if "SMARTCODEX_MANAGED_HOOK=notify" in command and "codex_notify.py" in command:
                            count += 1
                notify_command_counts[event] = count
            self.assertEqual(
                {
                    "SessionStart": 1,
                    "PermissionRequest": 1,
                    "SubagentStart": 1,
                    "SubagentStop": 1,
                    "Stop": 1,
                    "UserPromptSubmit": 1,
                },
                notify_command_counts,
            )

    def test_adopt_existing_backs_up_target_and_hooks_json_before_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hooks_json = home / ".codex" / "hooks.json"
            target_script = home / ".codex" / "hooks" / "codex_notify.py"
            hooks_json.parent.mkdir(parents=True)
            target_script.parent.mkdir(parents=True)
            target_script.write_text("# legacy notify hook\n", encoding="utf-8")
            original_config = {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python3 "$HOME/.codex/hooks/codex_notify.py"',
                                },
                                {
                                    "type": "command",
                                    "command": "python3 /user/stop.py",
                                },
                            ]
                        }
                    ],
                    "PermissionRequest": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 /user/permission.py",
                                }
                            ]
                        }
                    ],
                }
            }
            hooks_json.write_text(json.dumps(original_config, indent=2), encoding="utf-8")

            result = self.run_script(home, "install", "notify", "--adopt-existing")

            self.assertEqual(0, result.returncode, result.stderr)
            with (ROOT / "hooks" / "notify" / "codex_notify.py").open(encoding="utf-8") as f:
                self.assertEqual(f.read(), target_script.read_text(encoding="utf-8"))
            updated = self.load_hooks_json(home)
            self.assertIn("python3 /user/stop.py", json.dumps(updated))
            self.assertIn("python3 /user/permission.py", json.dumps(updated))
            self.assertNotIn('python3 "$HOME/.codex/hooks/codex_notify.py"', json.dumps(updated))
            managed_commands = self.managed_commands(updated)
            self.assertEqual(6, len(managed_commands))
            self.assertTrue(all("SMARTCODEX_MANAGED_HOOK=notify" in command for command in managed_commands))

            hooks_json_backups = sorted(hooks_json.parent.glob("hooks.json.smartcodex-backup-*"))
            self.assertEqual(1, len(hooks_json_backups))
            with hooks_json_backups[0].open(encoding="utf-8") as f:
                self.assertEqual(original_config, json.load(f))

            target_backups = sorted(target_script.parent.glob("codex_notify.py.smartcodex-backup-*"))
            self.assertEqual(1, len(target_backups))
            self.assertEqual("# legacy notify hook\n", target_backups[0].read_text(encoding="utf-8"))

    def test_adopt_existing_only_affects_selected_hook_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hooks_json = home / ".codex" / "hooks.json"
            target_script = home / ".codex" / "hooks" / "codex_notify.py"
            hooks_json.parent.mkdir(parents=True)
            target_script.parent.mkdir(parents=True)
            target_script.write_text("# legacy notify hook\n", encoding="utf-8")
            original_config = {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python3 "$HOME/.codex/hooks/codex_notify.py"',
                                },
                                {
                                    "type": "command",
                                    "command": "SMARTCODEX_MANAGED_HOOK=other python3 \"/tmp/other.py\"",
                                },
                            ]
                        }
                    ]
                }
            }
            hooks_json.write_text(json.dumps(original_config, indent=2), encoding="utf-8")

            result = self.run_script(home, "install", "notify", "--adopt-existing")

            self.assertEqual(0, result.returncode, result.stderr)
            updated = self.load_hooks_json(home)
            self.assertIn("SMARTCODEX_MANAGED_HOOK=other", json.dumps(updated))
            self.assertNotIn('python3 "$HOME/.codex/hooks/codex_notify.py"', json.dumps(updated))

    def test_adopt_existing_refuses_unrecognized_unmanaged_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hooks_json = home / ".codex" / "hooks.json"
            target_script = home / ".codex" / "hooks" / "codex_notify.py"
            hooks_json.parent.mkdir(parents=True)
            target_script.parent.mkdir(parents=True)
            target_script.write_text("# user owned notify hook\n", encoding="utf-8")
            original_config = {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 /user/custom_notify.py",
                                }
                            ]
                        }
                    ]
                }
            }
            hooks_json.write_text(json.dumps(original_config, indent=2), encoding="utf-8")

            result = self.run_script(home, "install", "notify", "--adopt-existing")

            self.assertNotEqual(0, result.returncode)
            self.assertIn("refusing to adopt unrecognized unmanaged hook target", result.stderr)
            self.assertEqual("# user owned notify hook\n", target_script.read_text(encoding="utf-8"))
            self.assertEqual(original_config, self.load_hooks_json(home))
            self.assertFalse(list(hooks_json.parent.glob("hooks.json.smartcodex-backup-*")))
            self.assertFalse(list(target_script.parent.glob("codex_notify.py.smartcodex-backup-*")))

    def test_adopt_existing_with_custom_target_refuses_when_only_default_duplicate_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            hooks_json = home / ".codex" / "hooks.json"
            default_target_script = home / ".codex" / "hooks" / "codex_notify.py"
            custom_target_dir = Path(tmp) / "custom hooks"
            custom_target_script = custom_target_dir / "codex_notify.py"
            hooks_json.parent.mkdir(parents=True)
            default_target_script.parent.mkdir(parents=True)
            custom_target_script.parent.mkdir(parents=True)
            default_target_script.write_text("# legacy notify hook\n", encoding="utf-8")
            custom_target_script.write_text("# custom user notify hook\n", encoding="utf-8")
            original_config = {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python3 "$HOME/.codex/hooks/codex_notify.py"',
                                }
                            ]
                        }
                    ]
                }
            }
            hooks_json.write_text(json.dumps(original_config, indent=2), encoding="utf-8")

            result = self.run_script(
                home,
                "install",
                "notify",
                "--target-dir",
                str(custom_target_dir),
                "--adopt-existing",
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("refusing to adopt unrecognized unmanaged hook target", result.stderr)
            self.assertEqual("# custom user notify hook\n", custom_target_script.read_text(encoding="utf-8"))
            self.assertEqual("# legacy notify hook\n", default_target_script.read_text(encoding="utf-8"))
            self.assertEqual(original_config, self.load_hooks_json(home))
            self.assertFalse(list(hooks_json.parent.glob("hooks.json.smartcodex-backup-*")))
            self.assertFalse(list(custom_target_dir.glob("codex_notify.py.smartcodex-backup-*")))

    def test_install_upgrades_managed_older_target_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hooks_json = home / ".codex" / "hooks.json"
            target_script = home / ".codex" / "hooks" / "codex_notify.py"
            hooks_json.parent.mkdir(parents=True)
            target_script.parent.mkdir(parents=True)
            target_script.write_text("# old SmartCodex notify hook\n", encoding="utf-8")
            original_config = {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f'SMARTCODEX_MANAGED_HOOK=notify python3 "{target_script}"',
                                }
                            ]
                        }
                    ]
                }
            }
            hooks_json.write_text(json.dumps(original_config, indent=2), encoding="utf-8")

            result = self.run_script(home, "install", "notify")

            self.assertEqual(0, result.returncode, result.stderr)
            with (ROOT / "hooks" / "notify" / "codex_notify.py").open(encoding="utf-8") as f:
                self.assertEqual(f.read(), target_script.read_text(encoding="utf-8"))
            updated = self.load_hooks_json(home)
            managed_commands = self.managed_commands(updated)
            self.assertEqual(6, len(managed_commands))
            self.assertTrue(all("SMARTCODEX_MANAGED_HOOK=notify" in command for command in managed_commands))
            backups = sorted(hooks_json.parent.glob("hooks.json.smartcodex-backup-*"))
            self.assertEqual(1, len(backups))
            with backups[0].open(encoding="utf-8") as f:
                self.assertEqual(original_config, json.load(f))

    def test_install_supports_explicit_target_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target_dir = Path(tmp) / "custom hook"

            result = self.run_script(home, "install", "notify", "--target-dir", str(target_dir))

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((target_dir / "codex_notify.py").exists())
            updated = self.load_hooks_json(home)
            command_text = json.dumps(updated)
            self.assertIn(str(target_dir / "codex_notify.py"), command_text)
            self.assertNotIn(str(home / ".codex" / "hooks" / "codex_notify.py"), command_text)

    def test_install_uses_default_codex_hooks_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            result = self.run_script(home, "install", "notify")

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((home / ".codex" / "hooks" / "codex_notify.py").exists())
            updated = self.load_hooks_json(home)
            self.assertIn(str(home / ".codex" / "hooks" / "codex_notify.py"), json.dumps(updated))

    def test_dry_run_does_not_write_target_hooks_json_or_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hooks_json = home / ".codex" / "hooks.json"
            hooks_json.parent.mkdir(parents=True)
            original_text = json.dumps({"hooks": {}}, indent=2)
            hooks_json.write_text(original_text, encoding="utf-8")

            result = self.run_script(home, "dry-run", "install", "notify")

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("would install notify", result.stdout)
            self.assertEqual(original_text, hooks_json.read_text(encoding="utf-8"))
            self.assertFalse(list(hooks_json.parent.glob("hooks.json.smartcodex-backup-*")))
            self.assertFalse((home / ".codex" / "hooks" / "codex_notify.py").exists())

    def test_dry_run_install_reports_managed_target_drift_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            install_result = self.run_script(home, "install", "notify")
            self.assertEqual(0, install_result.returncode, install_result.stderr)

            hooks_json = home / ".codex" / "hooks.json"
            target_script = home / ".codex" / "hooks" / "codex_notify.py"
            original_text = hooks_json.read_text(encoding="utf-8")
            target_script.write_text("# old managed SmartCodex notify hook\n", encoding="utf-8")

            result = self.run_script(home, "dry-run", "install", "notify")

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("would replace managed target: " + str(target_script.resolve()), result.stdout)
            self.assertIn("source differs from installed target", result.stdout)
            self.assertEqual("# old managed SmartCodex notify hook\n", target_script.read_text(encoding="utf-8"))
            self.assertEqual(original_text, hooks_json.read_text(encoding="utf-8"))
            self.assertFalse(list(hooks_json.parent.glob("hooks.json.smartcodex-backup-*")))

    def test_status_and_reinstall_handle_managed_helper_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            resources = self.codex_resources(tmp, "electron.icns")

            install_result = self.run_script(
                home,
                "install",
                "notify",
                extra_env={"SMARTCODEX_CODEX_RESOURCES_DIR": str(resources)},
            )
            self.assertEqual(0, install_result.returncode, install_result.stderr)

            hooks_json = home / ".codex" / "hooks.json"
            original_text = hooks_json.read_text(encoding="utf-8")
            helper_script = home / ".codex" / "hooks" / "notify_macos_helper" / "codex_macos_notify.py"
            helper_script.write_text("# old managed SmartCodex macOS helper\n", encoding="utf-8")

            status_result = self.run_script(home, "status", "--dry-run")
            self.assertEqual(0, status_result.returncode, status_result.stderr)
            self.assertIn("notify: installed, enabled, helper source drift", status_result.stdout)
            self.assertIn("helper target differs from manifest source: " + str(helper_script.resolve()), status_result.stdout)

            dry_run_result = self.run_script(home, "dry-run", "install", "notify")
            self.assertEqual(0, dry_run_result.returncode, dry_run_result.stderr)
            self.assertIn("would replace managed helper target: " + str(helper_script.resolve()), dry_run_result.stdout)
            self.assertEqual("# old managed SmartCodex macOS helper\n", helper_script.read_text(encoding="utf-8"))
            self.assertEqual(original_text, hooks_json.read_text(encoding="utf-8"))

            reinstall_result = self.run_script(
                home,
                "install",
                "notify",
                extra_env={"SMARTCODEX_CODEX_RESOURCES_DIR": str(resources)},
            )
            self.assertEqual(0, reinstall_result.returncode, reinstall_result.stderr)
            with (ROOT / "hooks" / "notify" / "macos_helper" / "codex_macos_notify.py").open(encoding="utf-8") as f:
                self.assertEqual(f.read(), helper_script.read_text(encoding="utf-8"))

    def test_disable_removes_only_smartcodex_entries_and_writes_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            install_result = self.run_script(home, "install", "notify")
            self.assertEqual(0, install_result.returncode, install_result.stderr)

            hooks_json = home / ".codex" / "hooks.json"
            updated = self.load_hooks_json(home)
            updated["hooks"]["PermissionRequest"].append(
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 /user/permission.py",
                        }
                    ]
                }
            )
            hooks_json.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")

            result = self.run_script(home, "disable", "notify")

            self.assertEqual(0, result.returncode, result.stderr)
            disabled = self.load_hooks_json(home)
            self.assertEqual([], self.managed_commands(disabled))
            self.assertIn("python3 /user/permission.py", json.dumps(disabled))
            self.assertGreaterEqual(len(list(hooks_json.parent.glob("hooks.json.smartcodex-backup-*"))), 1)

    def test_status_reports_installed_and_enabled_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            install_result = self.run_script(home, "install", "notify")
            self.assertEqual(0, install_result.returncode, install_result.stderr)

            result = self.run_script(home, "status")

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("notify: installed, enabled", result.stdout)

    def test_status_reports_managed_target_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            install_result = self.run_script(home, "install", "notify")
            self.assertEqual(0, install_result.returncode, install_result.stderr)

            target_script = home / ".codex" / "hooks" / "codex_notify.py"
            target_script.write_text("# old managed SmartCodex notify hook\n", encoding="utf-8")

            result = self.run_script(home, "status", "--dry-run")

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("notify: installed, enabled, source drift", result.stdout)
            self.assertIn("target differs from manifest source: " + str(target_script.resolve()), result.stdout)

    def test_status_accepts_manifest_option_and_dry_run_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hooks_json = home / ".codex" / "hooks.json"
            hooks_json.parent.mkdir(parents=True)
            original_text = json.dumps({"hooks": {}}, indent=2)
            hooks_json.write_text(original_text, encoding="utf-8")

            result = self.run_script(home, "status", "--manifest", str(MANIFEST_PATH), "--dry-run")

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("notify: not installed, disabled", result.stdout)
            self.assertEqual(original_text, hooks_json.read_text(encoding="utf-8"))
            self.assertFalse(list(hooks_json.parent.glob("hooks.json.smartcodex-backup-*")))
            self.assertFalse((home / ".codex" / "hooks" / "codex_notify.py").exists())


if __name__ == "__main__":
    unittest.main()
