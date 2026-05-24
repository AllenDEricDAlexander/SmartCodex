#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from contextlib import contextmanager


LOG_PATH = Path.home() / ".codex" / "logs" / "hooks" / "notify" / "codex_notify.jsonl"
STATE_PATH = Path.home() / ".codex" / "logs" / "hooks" / "notify" / "state.json"
STATE_LOCK_PATH = Path.home() / ".codex" / "logs" / "hooks" / "notify" / "state.lock"
HOOK_TIMEOUT_SECONDS = 5.0
CWD_DISPLAY_LENGTH = 64
PROJECT_DISPLAY_LENGTH = 48
AGENT_NAME_DISPLAY_LENGTH = 64
VISIBLE_MESSAGE_DISPLAY_LENGTH = 180
DEFAULT_MACOS_SAY_RATE = "300"
MACOS_HELPER_TIMEOUT_SECONDS = 1.0
ACTIVE_SUBAGENT_TTL_SECONDS = 6 * 60 * 60
DEDUP_WINDOW_SECONDS = 8.0
DETACHED_TITLE_DISPLAY_LENGTH = 120
DETACHED_BODY_DISPLAY_LENGTH = 800
DETACHED_SPEECH_DISPLAY_LENGTH = 240
DETACHED_EVENT_DISPLAY_LENGTH = 64
DETACHED_TOOL_NAME_DISPLAY_LENGTH = 160
DETACHED_NOTIFY_ARG = "--detached-notify"
DETACHED_NOTIFY_ENV = "CODEX_NOTIFY_DETACHED_PAYLOAD"
MACOS_NOTIFY_HELPER_ENV = "SMARTCODEX_NOTIFY_HELPER"
MACOS_NOTIFY_DISABLE_HELPER_ENV = "SMARTCODEX_NOTIFY_DISABLE_HELPER"
MOBILE_NOTIFY_URLS_ENV = "SMARTCODEX_MOBILE_NOTIFY_URLS"
MOBILE_NOTIFY_TIMEOUT_SECONDS = 2.0
MOBILE_NOTIFY_EVENTS = ("UserPromptSubmit", "Stop")
CTX_AGENT_FIELD = "_smartcodex_agent"
CTX_AGENT_SOURCE_FIELD = "_smartcodex_agent_source"
CTX_AGENT_SCOPE_FIELD = "_smartcodex_agent_scope"
CTX_SUPPRESS_FIELD = "_smartcodex_suppress_notify"


@dataclass(frozen=True)
class HookMessage:
    title: str
    body: str
    speech: str
    urgent: bool = False
    project_source: str = ""
    agent_source: str = ""
    agent_scope: str = "parent"


def log(entry: dict) -> None:
    try:
        record = dict(entry)
        record.setdefault("ts", datetime.now(timezone.utc).isoformat())
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


def event_summary(data: dict | None) -> dict:
    if not isinstance(data, dict):
        return {}

    summary = {}
    event = data.get("hook_event_name")
    tool = data.get("tool_name")
    if event:
        summary["event"] = str(event)
    if tool:
        summary["tool_name"] = str(tool)
    return summary


def message_summary(message: HookMessage) -> dict:
    return {
        "title": message.title,
        "urgent": message.urgent,
        "body_length": len(message.body),
        "speech_length": len(message.speech),
    }


def raw_summary(raw: str | None) -> dict:
    if raw is None:
        return {"present": False, "length": 0}
    return {"present": True, "length": len(raw)}


def log_backend_success(
    data: dict,
    message: HookMessage,
    project_source: str | None = None,
    agent_source: str | None = None,
) -> None:
    record = {"type": "backend_success", **event_summary(data)}
    record["message"] = message_summary(message)
    source = project_source if project_source is not None else message.project_source
    if source:
        record["project_source"] = shorten(source, 80)
    source = agent_source if agent_source is not None else message.agent_source
    if source:
        record["agent_source"] = shorten(source, 80)
    log(record)


def log_fallback(data: dict | None, reason: str) -> None:
    record = {"type": "fallback", **event_summary(data)}
    record["reason"] = shorten(reason, 120)
    log(record)


def log_error(where: str, exc: Exception, data: dict | None = None, raw: str | None = None, **fields) -> None:
    record = {
        "type": "error",
        "where": where,
        "error_type": type(exc).__name__,
        **event_summary(data),
    }
    if raw is not None:
        record["raw"] = raw_summary(raw)
    for key, value in fields.items():
        if value is not None:
            record[key] = shorten(value, 160)
    log(record)


def shorten(value, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def short_cwd(cwd) -> str:
    text = " ".join(str(cwd or "").split())
    if not text:
        return ""

    home = str(Path.home())
    if text == home:
        text = "~"
    elif text.startswith(home + os.sep):
        text = "~" + text[len(home):]

    if len(text) <= CWD_DISPLAY_LENGTH:
        return text

    parts = Path(text).parts
    if len(parts) >= 2:
        return shorten(".../" + "/".join(parts[-2:]), CWD_DISPLAY_LENGTH)
    return shorten(text, CWD_DISPLAY_LENGTH)


def project_label(cwd) -> str:
    text = " ".join(str(cwd or "").split())
    if not text:
        return ""
    return shorten(Path(text).name or text, PROJECT_DISPLAY_LENGTH)


def run_detached(cmd: list[str], env: dict | None = None) -> None:
    try:
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if env is not None:
            kwargs["env"] = env
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(cmd, **kwargs)
    except Exception as exc:
        backend = Path(str(cmd[0])).name if cmd else ""
        log_error("run_detached", exc, backend=backend)


def current_timestamp() -> float:
    return datetime.now(timezone.utc).timestamp()


@contextmanager
def state_lock():
    STATE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATE_LOCK_PATH.open("a", encoding="utf-8") as lock_file:
        try:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file, fcntl.LOCK_EX)
        except Exception as exc:
            log_error("state_lock", exc)
            yield
        else:
            try:
                yield
            finally:
                if os.name == "nt":
                    import msvcrt

                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file, fcntl.LOCK_UN)


def empty_state() -> dict:
    return {"active_subagents": [], "recent_notifications": {}}


def load_state_unlocked() -> dict:
    try:
        if not STATE_PATH.exists():
            return empty_state()
        with STATE_PATH.open(encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            return empty_state()
    except Exception as exc:
        log_error("load_state", exc)
        return empty_state()

    state.setdefault("active_subagents", [])
    state.setdefault("recent_notifications", {})
    if not isinstance(state["active_subagents"], list):
        state["active_subagents"] = []
    if not isinstance(state["recent_notifications"], dict):
        state["recent_notifications"] = {}
    return state


def save_state_unlocked(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = STATE_PATH.with_name(f"{STATE_PATH.name}.{os.getpid()}.tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, sort_keys=True)
        tmp_path.replace(STATE_PATH)
    except Exception as exc:
        log_error("save_state", exc)


def cleanup_state_unlocked(state: dict, now: float) -> None:
    active = []
    for entry in state.get("active_subagents", []):
        if not isinstance(entry, dict):
            continue
        started_at = safe_float(entry.get("started_at"))
        last_seen_at = safe_float(entry.get("last_seen_at")) or started_at
        if last_seen_at and now - last_seen_at <= ACTIVE_SUBAGENT_TTL_SECONDS:
            active.append(entry)
    state["active_subagents"] = active

    recent = {}
    for key, value in state.get("recent_notifications", {}).items():
        ts = safe_float(value)
        if ts and now - ts <= DEDUP_WINDOW_SECONDS:
            recent[str(key)] = ts
    state["recent_notifications"] = recent


def safe_float(value) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


@contextmanager
def speech_lock():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as lock_file:
        try:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file, fcntl.LOCK_EX)
        except Exception as exc:
            log_error("speech_lock", exc)
            yield
        else:
            try:
                yield
            finally:
                if os.name == "nt":
                    import msvcrt

                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file, fcntl.LOCK_UN)


def run_speech(cmd: list[str]) -> None:
    backend = Path(str(cmd[0])).name if cmd else ""
    try:
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with speech_lock():
            process = subprocess.Popen(cmd, **kwargs)
            process.wait()
    except Exception as exc:
        log_error("run_speech", exc, backend=backend)


def applescript_quote(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "  ")


def ps_quote(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def urgent_modal_enabled() -> bool:
    return os.environ.get("CODEX_NOTIFY_URGENT_MODAL", "").lower() in ("1", "true", "yes", "on")


def env_truthy(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes", "on")


def macos_notify_helper_path() -> str:
    if env_truthy(MACOS_NOTIFY_DISABLE_HELPER_ENV):
        return ""

    helper = os.environ.get(MACOS_NOTIFY_HELPER_ENV, "")
    if not helper:
        return ""

    path = Path(helper).expanduser()
    if path.is_file():
        return str(path)
    return ""


def run_macos_helper(helper: str, title: str, body: str) -> bool:
    try:
        result = subprocess.run(
            [
                sys.executable,
                helper,
                "--title",
                title,
                "--message",
                body,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=MACOS_HELPER_TIMEOUT_SECONDS,
            check=False,
        )
        return result.returncode == 0
    except Exception as exc:
        log_error("run_macos_helper", exc, backend=Path(helper).name)
        return False


def speak(text: str) -> None:
    system = platform.system()

    if system == "Darwin" and shutil.which("say"):
        cmd = ["say"]
        voice = os.environ.get("CODEX_NOTIFY_VOICE")
        rate = os.environ.get("CODEX_NOTIFY_RATE") or DEFAULT_MACOS_SAY_RATE
        if voice:
            cmd += ["-v", voice]
        cmd += ["-r", rate]
        cmd.append(text)
        run_speech(cmd)
        return

    if system == "Linux":
        if shutil.which("spd-say"):
            run_speech(["spd-say", "--wait", text])
            return
        if shutil.which("espeak"):
            run_speech(["espeak", text])
            return

    if system == "Windows":
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
        if powershell:
            script = (
                "$voice = New-Object -ComObject SAPI.SpVoice; "
                f"$voice.Speak({ps_quote(text)}) | Out-Null"
            )
            run_speech([powershell, "-NoProfile", "-Command", script])


def notify_macos(title: str, body: str, urgent: bool) -> None:
    helper = macos_notify_helper_path()
    has_osascript = shutil.which("osascript")
    sent_notification = False

    if helper:
        sent_notification = run_macos_helper(helper, title, body)

    safe_title = applescript_quote(title)
    safe_body = applescript_quote(body)

    if not sent_notification and has_osascript:
        run_detached([
            "osascript",
            "-e",
            f'display notification "{safe_body}" with title "{safe_title}"'
        ])

    if urgent and urgent_modal_enabled() and has_osascript:
        dialog = (
            "beep 2\n"
            f'display dialog "{safe_body}" '
            f'with title "{safe_title}" '
            'buttons {"去 Codex 处理"} '
            'default button "去 Codex 处理" '
            'with icon caution '
            'giving up after 30'
        )
        run_detached(["osascript", "-e", dialog])


def notify_linux(title: str, body: str, urgent: bool) -> None:
    if shutil.which("notify-send"):
        urgency = "critical" if urgent else "normal"
        run_detached(["notify-send", "-u", urgency, title, body])

    if urgent and urgent_modal_enabled():
        if shutil.which("zenity"):
            run_detached([
                "zenity",
                "--warning",
                f"--title={title}",
                f"--text={body}",
                "--timeout=30",
            ])
        elif shutil.which("kdialog"):
            run_detached(["kdialog", "--title", title, "--msgbox", body])


def notify_windows(title: str, body: str, urgent: bool) -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not powershell:
        return

    balloon = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Information
$notify.Visible = $true
$notify.ShowBalloonTip(12000, {ps_quote(title)}, {ps_quote(body)}, [System.Windows.Forms.ToolTipIcon]::Info)
Start-Sleep -Seconds 13
$notify.Dispose()
"""
    run_detached([powershell, "-NoProfile", "-Command", balloon])

    if urgent and urgent_modal_enabled():
        popup = (
            "$shell = New-Object -ComObject WScript.Shell; "
            f"$shell.Popup({ps_quote(body)}, 30, {ps_quote(title)}, 0x40) | Out-Null"
        )
        run_detached([powershell, "-NoProfile", "-Command", popup])


def notify(title: str, body: str, speech: str, urgent: bool = False) -> None:
    system = platform.system()

    if system == "Darwin":
        notify_macos(title, body, urgent)
    elif system == "Linux":
        notify_linux(title, body, urgent)
    elif system == "Windows":
        notify_windows(title, body, urgent)

    if speech:
        speak(speech)


def mobile_notify_urls() -> list[str]:
    raw = os.environ.get(MOBILE_NOTIFY_URLS_ENV, "")
    if not raw.strip():
        return []
    normalized = raw.replace("\n", ",")
    return [part.strip() for part in normalized.split(",") if part.strip()]


def mobile_notification_enabled(data: dict | None) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get(CTX_AGENT_SCOPE_FIELD) == "subagent":
        return False
    return data.get("hook_event_name") in MOBILE_NOTIFY_EVENTS


def mobile_payload(data: dict, message: HookMessage) -> dict:
    return {
        "event": shorten(data.get("hook_event_name", ""), DETACHED_EVENT_DISPLAY_LENGTH),
        "title": message.title,
        "message": message.body,
    }


def send_mobile_webhook(url: str, payload: dict) -> None:
    try:
        if "://ntfy.sh/" in url:
            body = str(payload.get("message", "")).encode("utf-8")
            headers = {
                "Content-Type": "text/plain; charset=utf-8",
                "Title": str(payload.get("title", "")),
            }
        else:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json; charset=utf-8"}
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=MOBILE_NOTIFY_TIMEOUT_SECONDS):
            pass
    except Exception as exc:
        log_error("send_mobile_webhook", exc, event=payload.get("event"), backend=url)


def notify_mobile(data: dict | None, message: HookMessage | None) -> None:
    if not message or not mobile_notification_enabled(data):
        return
    payload = mobile_payload(data, message)
    for url in mobile_notify_urls():
        send_mobile_webhook(url, payload)


def send_message(data: dict, message: HookMessage) -> None:
    notify(message.title, message.body, message.speech, urgent=message.urgent)
    notify_mobile(data, message)


def detached_payload(data: dict, message: HookMessage) -> str:
    payload = {
        "event": shorten(data.get("hook_event_name", ""), DETACHED_EVENT_DISPLAY_LENGTH),
        "tool_name": shorten(data.get("tool_name", ""), DETACHED_TOOL_NAME_DISPLAY_LENGTH),
        "agent_scope": shorten(data.get(CTX_AGENT_SCOPE_FIELD, message.agent_scope), 24),
        "message": {
            "title": shorten(message.title, DETACHED_TITLE_DISPLAY_LENGTH),
            "body": shorten(message.body, DETACHED_BODY_DISPLAY_LENGTH),
            "speech": shorten(message.speech, DETACHED_SPEECH_DISPLAY_LENGTH),
            "urgent": message.urgent,
            "project_source": shorten(message.project_source, 80),
            "agent_source": shorten(message.agent_source, 80),
            "agent_scope": shorten(message.agent_scope, 24),
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def message_from_detached_payload(payload: dict) -> HookMessage:
    message = payload.get("message", {})
    return HookMessage(
        title=str(message.get("title", "")),
        body=str(message.get("body", "")),
        speech=str(message.get("speech", "")),
        urgent=bool(message.get("urgent", False)),
        project_source=str(message.get("project_source", "")),
        agent_source=str(message.get("agent_source", "")),
        agent_scope=str(message.get("agent_scope", "parent")),
    )


def send_message_detached(data: dict, message: HookMessage) -> None:
    env = os.environ.copy()
    env[DETACHED_NOTIFY_ENV] = detached_payload(data, message)
    run_detached([sys.executable, str(Path(__file__).resolve()), DETACHED_NOTIFY_ARG], env=env)


def run_detached_notification() -> int:
    raw = os.environ.get(DETACHED_NOTIFY_ENV, "")
    try:
        payload = json.loads(raw)
        message = message_from_detached_payload(payload)
        send_message(
            {
                "hook_event_name": payload.get("event", ""),
                "tool_name": payload.get("tool_name", ""),
                CTX_AGENT_SCOPE_FIELD: payload.get("agent_scope", ""),
            },
            message,
        )
        log_backend_success(
            {
                "hook_event_name": payload.get("event", ""),
                "tool_name": payload.get("tool_name", ""),
                CTX_AGENT_SCOPE_FIELD: payload.get("agent_scope", ""),
            },
            message,
        )
    except Exception as exc:
        log_error("run_detached_notification", exc, raw=raw)
    return 0


def read_hook_input() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception as exc:
        log_error("read_hook_input", exc, raw=raw)
        return {}


def is_agent_completion_event(data: dict) -> bool:
    event = data.get("hook_event_name", "")
    return event == "SubagentStop"


def uses_json_stdout(data: dict) -> bool:
    return data.get("hook_event_name", "") in ("Stop", "SubagentStop")


def write_json_stdout() -> None:
    # Stop/SubagentStop 必须给 Codex 返回合法 JSON；通知和语音走脱离 hook 的子进程。
    sys.stdout.write("{}")
    sys.stdout.flush()


def state_value(value) -> str:
    return " ".join(str(value or "").split())


def state_entry_from_subagent_start(data: dict, now: float) -> dict:
    agent, agent_source = payload_agent(data)
    return {
        "agent": agent,
        "agent_source": agent_source,
        "agent_id": state_value(data.get("agent_id")),
        "session_id": state_value(data.get("session_id")),
        "turn_id": state_value(data.get("turn_id")),
        "cwd": state_value(data.get("cwd")),
        "started_at": now,
        "last_seen_at": now,
    }


def entry_context_matches(entry: dict, data: dict) -> bool:
    compared = False
    for field in ("agent_id", "session_id", "cwd"):
        entry_value = state_value(entry.get(field))
        data_value = state_value(data.get(field))
        if entry_value and data_value:
            compared = True
            if entry_value != data_value:
                return False
    return compared


def entry_match_score(entry: dict, data: dict) -> tuple[int, float]:
    score = 0
    for field, weight in (("agent_id", 100), ("session_id", 20), ("cwd", 10), ("turn_id", 5)):
        entry_value = state_value(entry.get(field))
        data_value = state_value(data.get(field))
        if entry_value and data_value and entry_value == data_value:
            score += weight
    agent, _ = payload_agent(data)
    if agent != "subagent" and agent == state_value(entry.get("agent")):
        score += 3
    return score, safe_float(entry.get("last_seen_at")) or safe_float(entry.get("started_at"))


def find_active_subagent(state: dict, data: dict) -> dict | None:
    candidates = [
        entry
        for entry in state.get("active_subagents", [])
        if isinstance(entry, dict) and entry_context_matches(entry, data)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda entry: entry_match_score(entry, data))


def remove_active_subagent(state: dict, target: dict | None) -> None:
    if not target:
        return
    state["active_subagents"] = [
        entry
        for entry in state.get("active_subagents", [])
        if entry is not target and entry != target
    ]


def upsert_active_subagent(state: dict, entry: dict) -> None:
    kept = []
    for existing in state.get("active_subagents", []):
        if not isinstance(existing, dict):
            continue
        same_agent_id = entry.get("agent_id") and existing.get("agent_id") == entry.get("agent_id")
        same_context = (
            existing.get("session_id") == entry.get("session_id")
            and existing.get("cwd") == entry.get("cwd")
            and existing.get("agent") == entry.get("agent")
        )
        if same_agent_id or same_context:
            continue
        kept.append(existing)
    kept.append(entry)
    state["active_subagents"] = kept


def annotate_agent_context(data: dict, agent: str, source: str, scope: str) -> None:
    data[CTX_AGENT_FIELD] = shorten(agent, AGENT_NAME_DISPLAY_LENGTH)
    data[CTX_AGENT_SOURCE_FIELD] = source
    data[CTX_AGENT_SCOPE_FIELD] = scope


def apply_event_state(state: dict, data: dict, now: float) -> dict:
    event = data.get("hook_event_name", "")
    contextual = dict(data)

    if event == "SubagentStart":
        entry = state_entry_from_subagent_start(data, now)
        upsert_active_subagent(state, entry)
        annotate_agent_context(contextual, entry["agent"], entry["agent_source"], "subagent")
        return contextual

    if event == "SubagentStop":
        entry = find_active_subagent(state, data)
        agent, source = payload_agent(data)
        if agent == "subagent" and entry:
            agent = state_value(entry.get("agent")) or agent
            source = state_value(entry.get("agent_source")) or "state"
        remove_active_subagent(state, entry)
        annotate_agent_context(contextual, agent, source, "subagent")
        return contextual

    if event == "Stop":
        entry = find_active_subagent(state, data)
        if entry:
            agent = state_value(entry.get("agent")) or "subagent"
            source = state_value(entry.get("agent_source")) or "state"
            remove_active_subagent(state, entry)
            annotate_agent_context(contextual, agent, source, "subagent")
        else:
            annotate_agent_context(contextual, "parent", "parent_event", "parent")
        return contextual

    if event == "UserPromptSubmit":
        entry = find_active_subagent(state, data)
        if entry:
            entry["last_seen_at"] = now
            agent = state_value(entry.get("agent")) or "subagent"
            source = state_value(entry.get("agent_source")) or "state"
            annotate_agent_context(contextual, agent, source, "subagent")
        else:
            annotate_agent_context(contextual, "parent", "parent_event", "parent")
        return contextual

    annotate_agent_context(contextual, "parent", "parent_event", "parent")
    return contextual


def notification_dedupe_key(data: dict, message: HookMessage) -> str:
    project = visible_project(data)
    agent = visible_agent(data)
    action = display_action(data)
    scope = resolved_agent_scope(data)
    return "|".join((scope, project, agent, action))


def should_emit_message_unlocked(state: dict, data: dict, message: HookMessage, now: float) -> bool:
    key = notification_dedupe_key(data, message)
    last_ts = safe_float(state.get("recent_notifications", {}).get(key))
    if last_ts and now - last_ts <= DEDUP_WINDOW_SECONDS:
        return False
    state.setdefault("recent_notifications", {})[key] = now
    return True


def prepare_event_data(data: dict) -> tuple[dict, bool]:
    with state_lock():
        now = current_timestamp()
        state = load_state_unlocked()
        cleanup_state_unlocked(state, now)
        contextual = apply_event_state(state, data, now)
        message = build_message(contextual)
        emit = bool(message) and should_emit_message_unlocked(state, contextual, message, now)
        save_state_unlocked(state)
    return contextual, emit


def resolved_project(data: dict) -> tuple[str, str]:
    project = project_label(data.get("cwd"))
    if project:
        return project, "payload.cwd"

    try:
        project = project_label(Path.cwd())
        if project:
            return project, "process.cwd"
    except Exception:
        pass

    project = project_label(os.environ.get("PWD"))
    if project:
        return project, "env.PWD"

    return "Unknown project", "unknown"


def visible_project(data: dict) -> str:
    return resolved_project(data)[0]


def is_uuid_like(value) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = text.replace("-", "")
    if len(compact) < 16:
        return False
    return all(char in "0123456789abcdef" for char in compact)


def payload_agent(data: dict) -> tuple[str, str]:
    for field in ("agent_type", "subagent_type", "agent_name", "name"):
        value = data.get(field)
        if value and not is_uuid_like(value):
            return shorten(value, AGENT_NAME_DISPLAY_LENGTH), field
    return "subagent", "fallback"


def resolved_agent(data: dict) -> tuple[str, str]:
    contextual_agent = data.get(CTX_AGENT_FIELD)
    if contextual_agent:
        return shorten(contextual_agent, AGENT_NAME_DISPLAY_LENGTH), str(data.get(CTX_AGENT_SOURCE_FIELD) or "context")

    event = data.get("hook_event_name", "")
    if event in ("SubagentStart", "SubagentStop"):
        return payload_agent(data)

    return "parent", "parent_event"


def resolved_agent_scope(data: dict) -> str:
    scope = data.get(CTX_AGENT_SCOPE_FIELD)
    if scope in ("parent", "subagent"):
        return str(scope)
    event = data.get("hook_event_name", "")
    if event in ("SubagentStart", "SubagentStop"):
        return "subagent"
    return "parent"


def visible_agent(data: dict) -> str:
    return resolved_agent(data)[0]


def display_action(data: dict) -> str:
    event = data.get("hook_event_name", "")
    if event == "SessionStart":
        return "session started"
    if event in ("UserPromptSubmit", "SubagentStart"):
        return "task started"
    if event in ("Stop", "SubagentStop"):
        return "task finished"
    if event == "PermissionRequest":
        tool = shorten(data.get("tool_name") or "unknown tool", 80)
        return f'permission requested for "{tool}"'
    return ""


def speech_action(data: dict) -> str:
    if data.get("hook_event_name", "") == "SessionStart":
        return ""
    return display_action(data)


def visible_message_text(data: dict) -> str:
    action = display_action(data)
    if not action:
        return ""

    text = f'Project "{visible_project(data)}", agent "{visible_agent(data)}", {action}.'
    return shorten(text, VISIBLE_MESSAGE_DISPLAY_LENGTH)


def speech_message_text(data: dict) -> str:
    action = speech_action(data)
    if not action:
        return ""

    text = f'Project "{visible_project(data)}", agent "{visible_agent(data)}", {action}.'
    return shorten(text, VISIBLE_MESSAGE_DISPLAY_LENGTH)


def visible_hook_message(data: dict, urgent: bool = False) -> HookMessage | None:
    display = visible_message_text(data)
    speech = speech_message_text(data)
    if not display:
        return None
    return HookMessage(
        title=display,
        body=display,
        speech=speech,
        urgent=urgent,
        project_source=resolved_project(data)[1],
        agent_source=resolved_agent(data)[1],
        agent_scope=resolved_agent_scope(data),
    )


def build_message(data: dict) -> HookMessage | None:
    event = data.get("hook_event_name", "")

    if event in ("SessionStart", "UserPromptSubmit", "Stop", "SubagentStart", "SubagentStop", "PermissionRequest"):
        return visible_hook_message(data, urgent=(event == "PermissionRequest"))

    return None


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == DETACHED_NOTIFY_ARG:
        return run_detached_notification()

    data = read_hook_input()
    event = data.get("hook_event_name", "")
    wrote_json_stdout = False

    try:
        message_data, should_emit = prepare_event_data(data)
        if uses_json_stdout(data):
            message = build_message(message_data) if should_emit else None
            write_json_stdout()
            wrote_json_stdout = True
            if message:
                send_message_detached(message_data, message)
            return 0

        message = build_message(message_data) if should_emit else None
        if message:
            send_message(message_data, message)
            log_backend_success(message_data, message)
        else:
            log_fallback(message_data, "no_message_for_event_or_duplicate")

    except Exception as exc:
        log_error("main", exc, {"hook_event_name": event})
        if uses_json_stdout(data) and not wrote_json_stdout:
            write_json_stdout()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
