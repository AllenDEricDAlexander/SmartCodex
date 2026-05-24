# SmartCodex

SmartCodex contains local Codex helper scripts and tooling.

## Hooks

Codex hook sources live under `hooks/`. Installable hooks are declared in
`hooks/hooks.manifest.json`, so future hook scripts can exist in the repository
without being enabled in Codex.

- `hooks/notify/codex_notify.py`: desktop notification hook for Codex permission,
  parent turn, and subagent lifecycle events.
- `hooks/notify/macos_helper/`: local macOS notification helper used to show
  notifications with the Codex icon when available.
- `scripts/update_hooks.py`: manifest-driven installer and `hooks.json` updater.
- `scripts/update_hooks.sh`: shell wrapper for daily use.

The repository runtime source is `hooks/notify/codex_notify.py`. The installer
copies that source to the configured Codex hook directory as `codex_notify.py`;
there is no root-level compatibility entrypoint.

The notify hook writes runtime logs to:

```text
~/.codex/logs/hooks/notify/codex_notify.jsonl
```

The log is JSONL and stores safe summaries only. It records event names, backend
success/fallback/error state, and message lengths, but does not persist full
`tool_input`, prompt, transcript, or assistant-message payloads.

The notify hook is registered for these Codex events:

- `SessionStart`: session started, resumed, cleared, or compacted.
- `UserPromptSubmit`: parent task/turn started.
- `Stop`: parent task/turn finished.
- `PermissionRequest`: Codex is waiting for permission.
- `SubagentStart`: subagent started.
- `SubagentStop`: subagent finished.

Notification title and body use English-only text:

```text
Project "<project>", agent "<agent>", <action>.
```

Speech uses the same wording so the spoken reminder matches the popup:

```text
Project "<project>", agent "<agent>", <action>.
```

`SessionStart` is visual-only by default. It still creates a desktop
notification, but it does not speak, because Codex often emits `SessionStart`
immediately before `UserPromptSubmit` when a thread resumes.

The project is resolved from the hook payload `cwd`, then the hook process
working directory, then `PWD`. Parent events use `parent`; subagent events use
`agent_type`, then `subagent_type`, then a safe non-id name, and finally
`subagent`. UUID-like ids are never used as visible agent names.

Actions are mapped as:

- `SessionStart`: `session started`
- `UserPromptSubmit`: `task started`
- `Stop`: `task finished`
- `PermissionRequest`: `permission requested for "<tool>"`
- `SubagentStart`: `task started`
- `SubagentStop`: `task finished`

Examples:

```text
Project "SmartReader", agent "parent", session started.
Project "SmartReader", agent "parent", task started.
Project "SmartReader", agent "parent", permission requested for "Bash".
Project "SmartReader", agent "fullstack-developer", task started.
Project "SmartReader", agent "fullstack-developer", task finished.
Project "SmartReader", agent "parent", task finished.
```

Internal identifiers are log-only. Notifications and speech never include
dialog, thread, session, turn, or agent ids, and never include full prompts,
commands, transcripts, assistant messages, or raw `tool_input` values.

The hook keeps a small local state file at
`~/.codex/logs/hooks/notify/state.json` to connect `SubagentStart` with later
events. If Codex emits `Stop` instead of `SubagentStop` when a subagent returns
to the parent, the hook reports the active subagent as finished and then clears
that state. Immediate duplicate subagent start notifications are suppressed.

`Stop` and `SubagentStop` write valid JSON stdout before notification side
effects. Completion notifications are sent through a detached path so Codex does
not wait on speech or desktop notification work before finishing the hook.

On macOS, permission requests use non-blocking notifications by default. Modal
permission dialogs are opt-in with:

```bash
CODEX_NOTIFY_URGENT_MODAL=1
```

On macOS, the installer writes `SMARTCODEX_NOTIFY_HELPER` into each managed hook
command. The runtime sends notifications through the generated
`CodexNotify.app` helper by default. The helper is shown to macOS as
`Codex Notify` (`com.smartcodex.notify`). It is a native Swift app that calls
macOS UserNotifications directly, carries the copied Codex icon, and avoids the
script editor icon used by plain `osascript`. If the helper cannot launch or
macOS denies notification permission, the runtime falls back to
`osascript display notification` so the user still gets a popup even when the
icon-specific path fails.

For the correct Codex icon, allow notifications for `Codex Notify` in macOS
System Settings > Notifications. During migration from older helper builds,
macOS may also show `Codex` and `CodexNotify`; those can stay enabled, but the
current helper identity is `Codex Notify`. Older gray-icon notifications already
shown in Notification Center are stale fallback notifications and do not prove
the current helper icon is wrong.

Disable the helper and use the plain `osascript display notification` fallback
with:

```bash
SMARTCODEX_NOTIFY_DISABLE_HELPER=1
```

The installer compiles `hooks/notify/macos_helper/CodexNotifyApp.swift` into
`CodexNotify.app`. If `swiftc` is unavailable, it falls back to the
AppleScript applet source. The installer reads Codex.app's `CFBundleIconFile`,
copies that `.icns` into the helper app, and falls back to `icon.icns` or
`electron.icns` if the bundle metadata is unavailable. The helper opens the app
in the background with `open -g -n`; it is not launched hidden so macOS can still
surface the notification.

Mobile push notifications are optional and webhook-based. Codex hooks currently
run local command handlers; there is no documented ChatGPT mobile push API for a
local hook to call directly. Configure a mobile webhook endpoint such as `ntfy`
with:

```bash
SMARTCODEX_MOBILE_NOTIFY_URLS=https://ntfy.sh/<your-private-topic>
```

The mobile channel only sends parent task start and finish events:
`UserPromptSubmit` and `Stop`. It intentionally skips `SessionStart`,
`PermissionRequest`, and all subagent events.

Speech playback is serialized across hook processes so multiple reminders do
not speak over each other. macOS speech uses `say -r 300` by default. Override
the voice or speed with:

```bash
CODEX_NOTIFY_VOICE=Mei-Jia
CODEX_NOTIFY_RATE=240
```

## Hook Management

List available managed hooks:

```bash
sh scripts/update_hooks.sh list
```

Check install and enablement state:

```bash
sh scripts/update_hooks.sh status
```

`status` reports `source drift` when the installed live target differs from the
manifest source in the repository. It reports `helper source drift` when an
installed helper file differs from its repository source. Either state means
Codex may still be running old notification code.

Preview an install without writing files:

```bash
sh scripts/update_hooks.sh dry-run install notify
```

When the live target or helper is stale, `dry-run install` reports that it would
replace the managed target. After a successful install, `status` should return
`notify: installed, enabled` without drift.

Install and enable the notify hook:

```bash
sh scripts/update_hooks.sh install notify
```

This copies `hooks/notify/codex_notify.py` to `~/.codex/hooks/codex_notify.py`,
installs the macOS helper under `~/.codex/hooks/notify_macos_helper`, injects
`SMARTCODEX_NOTIFY_HELPER` into all managed notify commands, and backs up
`~/.codex/hooks.json` before writing.

If you already had a manually installed notify hook, `status` or `dry-run` may
report duplicate legacy entries such as `python3 "$HOME/.codex/hooks/codex_notify.py"`.
Codex loads matching hooks from multiple sources, so leaving duplicates can
produce duplicate or stale notifications.

Preview the safe cleanup/adoption path:

```bash
sh scripts/update_hooks.sh dry-run install notify --adopt-existing
```

Apply it:

```bash
sh scripts/update_hooks.sh install notify --adopt-existing
```

`--adopt-existing` only migrates recognized legacy SmartCodex notify entries for
the selected hook. It backs up `~/.codex/hooks.json` before changing it, backs up
the replaced target script when needed, preserves unrelated hooks, and refuses to
adopt an unrelated custom target.

Disable the managed notify entries in `~/.codex/hooks.json` without deleting the
installed script:

```bash
sh scripts/update_hooks.sh disable notify
```

The default install target is:

```text
~/.codex/hooks
```

Use `--target-dir` when a different Codex hook script directory is needed:

```bash
sh scripts/update_hooks.sh install notify --target-dir "$HOME/.codex/hook"
```

`update_hooks.py` preserves non-SmartCodex hook entries, updates only commands
marked with `SMARTCODEX_MANAGED_HOOK=<id>`, and backs up `~/.codex/hooks.json`
before writing. It refuses to overwrite a non-identical unmanaged target script,
but allows upgrades when the existing `hooks.json` proves the target is already
managed by SmartCodex. Dry-run uses the same blocker checks as a real install,
so unsafe installs are reported before any write happens.

The Python entrypoint is also available directly:

```bash
python3 scripts/update_hooks.py list --manifest hooks/hooks.manifest.json
python3 scripts/update_hooks.py status --manifest hooks/hooks.manifest.json --dry-run
```

## Checks

Run focused tests:

```bash
python3 -B -m pytest -p no:cacheprovider tests/hooks/notify/test_codex_notify.py tests/scripts/test_update_hooks.py
```

Run syntax checks without writing bytecode under the macOS user cache:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/smartcodex-pycache python3 -m py_compile hooks/notify/codex_notify.py hooks/notify/macos_helper/codex_macos_notify.py scripts/update_hooks.py
```
