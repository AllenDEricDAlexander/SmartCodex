# SmartCodex

SmartCodex contains local Codex helper scripts and tooling.

## Hooks

Codex hook sources live under `hooks/`. Installable hooks are declared in
`hooks/hooks.manifest.json`, so future hook scripts can exist in the repository
without being enabled in Codex.

- `hooks/notify/codex_notify.py`: desktop notification hook for Codex permission,
  parent turn, and subagent lifecycle events.
- `codex_notify.py`: compatibility entrypoint that runs the notify script above.
- `scripts/update_hooks.py`: manifest-driven installer and `hooks.json` updater.
- `scripts/update_hooks.sh`: shell wrapper for daily use.

The notify hook writes runtime logs to:

```text
~/.codex/logs/hooks/notify/codex_notify.jsonl
```

The log is JSONL and stores safe summaries only. It records event names, backend
success/fallback/error state, and message lengths, but does not persist full
`tool_input`, prompt, transcript, or assistant-message payloads.

The notify hook is registered for these Codex events:

- `UserPromptSubmit`: parent task/turn started.
- `Stop`: parent task/turn finished.
- `PermissionRequest`: Codex is waiting for permission.
- `SubagentStart`: subagent started.
- `SubagentStop`: subagent finished.

Notification text includes safe context when Codex provides it: project name from
`cwd`, shortened cwd, shortened `session_id`, shortened `turn_id`, and shortened
agent id/type. It intentionally does not display full prompts, commands,
transcripts, assistant messages, or raw `tool_input` values.

`Stop` and `SubagentStop` write valid JSON stdout before notification side
effects. Completion notifications are sent through a detached path so Codex does
not wait on speech or desktop notification work before finishing the hook.

On macOS, permission requests use non-blocking notifications by default. Modal
permission dialogs are opt-in with:

```bash
CODEX_NOTIFY_URGENT_MODAL=1
```

Speech playback is serialized across hook processes and uses bounded cleanup so
multiple reminders do not speak over each other.

## Hook Management

List available managed hooks:

```bash
sh scripts/update_hooks.sh list
```

Check install and enablement state:

```bash
sh scripts/update_hooks.sh status
```

Preview an install without writing files:

```bash
sh scripts/update_hooks.sh dry-run install notify
```

Install and enable the notify hook:

```bash
sh scripts/update_hooks.sh install notify
```

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
python3 -m pytest tests/hooks/notify/test_codex_notify.py tests/scripts/test_update_hooks.py
```

Run syntax checks without writing bytecode under the macOS user cache:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/smartcodex-pycache python3 -m py_compile codex_notify.py hooks/notify/codex_notify.py scripts/update_hooks.py
```
