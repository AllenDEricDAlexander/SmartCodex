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
from pathlib import Path
from contextlib import contextmanager


LOG_PATH = Path.home() / ".codex" / "logs" / "hooks" / "notify" / "codex_notify.jsonl"
HOOK_TIMEOUT_SECONDS = 5.0
SPEECH_WAIT_TIMEOUT_SECONDS = 4.0
SPEECH_TERMINATE_TIMEOUT_SECONDS = 0.5
SPEECH_KILL_TIMEOUT_SECONDS = 0.2
SESSION_ID_DISPLAY_LENGTH = 12
TURN_ID_DISPLAY_LENGTH = 11
AGENT_ID_DISPLAY_LENGTH = 12
CWD_DISPLAY_LENGTH = 64
PROJECT_DISPLAY_LENGTH = 48
AGENT_NAME_DISPLAY_LENGTH = 64
DETACHED_TITLE_DISPLAY_LENGTH = 120
DETACHED_BODY_DISPLAY_LENGTH = 800
DETACHED_SPEECH_DISPLAY_LENGTH = 240
DETACHED_EVENT_DISPLAY_LENGTH = 64
DETACHED_TOOL_NAME_DISPLAY_LENGTH = 160
DETACHED_NOTIFY_ARG = "--detached-notify"
DETACHED_NOTIFY_ENV = "CODEX_NOTIFY_DETACHED_PAYLOAD"


@dataclass(frozen=True)
class HookMessage:
    title: str
    body: str
    speech: str
    urgent: bool = False


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


def log_backend_success(data: dict, message: HookMessage) -> None:
    record = {"type": "backend_success", **event_summary(data)}
    record["message"] = message_summary(message)
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


def short_id(value, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


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


def context_lines(data: dict) -> list[str]:
    # 只使用 hook 的安全上下文字段，避免把 prompt/转写/工具输入带进通知。
    lines = []
    project = project_label(data.get("cwd"))
    cwd = short_cwd(data.get("cwd"))
    if project:
        line = f"项目：{project}"
        if cwd and cwd != project:
            line += f" ({cwd})"
        lines.append(line)

    session_id = short_id(data.get("session_id"), SESSION_ID_DISPLAY_LENGTH)
    turn_id = short_id(data.get("turn_id"), TURN_ID_DISPLAY_LENGTH)
    if session_id:
        lines.append(f"会话：{session_id}")
    if turn_id:
        lines.append(f"回合：{turn_id}")
    return lines


def body_text(lines: list[str], data: dict) -> str:
    body_lines = list(lines)
    body_lines.extend(context_lines(data))
    return "\n".join(body_lines)


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
            try:
                process.wait(timeout=SPEECH_WAIT_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as exc:
                log_error("run_speech_timeout", exc, backend=backend)
                try:
                    process.terminate()
                    process.wait(timeout=SPEECH_TERMINATE_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired as terminate_exc:
                    log_error("run_speech_terminate_timeout", terminate_exc, backend=backend)
                    try:
                        process.kill()
                        process.wait(timeout=SPEECH_KILL_TIMEOUT_SECONDS)
                    except Exception as kill_exc:
                        log_error("run_speech_kill", kill_exc, backend=backend)
                except Exception as terminate_exc:
                    log_error("run_speech_terminate", terminate_exc, backend=backend)
    except Exception as exc:
        log_error("run_speech", exc, backend=backend)


def applescript_quote(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "  ")


def ps_quote(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def urgent_modal_enabled() -> bool:
    return os.environ.get("CODEX_NOTIFY_URGENT_MODAL", "").lower() in ("1", "true", "yes", "on")


def speak(text: str) -> None:
    system = platform.system()

    if system == "Darwin" and shutil.which("say"):
        cmd = ["say"]
        voice = os.environ.get("CODEX_NOTIFY_VOICE")
        rate = os.environ.get("CODEX_NOTIFY_RATE")
        if voice:
            cmd += ["-v", voice]
        if rate:
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
    if not shutil.which("osascript"):
        return

    safe_title = applescript_quote(title)
    safe_body = applescript_quote(body)

    run_detached([
        "osascript",
        "-e",
        f'display notification "{safe_body}" with title "{safe_title}"'
    ])

    if urgent and urgent_modal_enabled():
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

    speak(speech)


def send_message(message: HookMessage) -> None:
    notify(message.title, message.body, message.speech, urgent=message.urgent)


def detached_payload(data: dict, message: HookMessage) -> str:
    payload = {
        "event": shorten(data.get("hook_event_name", ""), DETACHED_EVENT_DISPLAY_LENGTH),
        "tool_name": shorten(data.get("tool_name", ""), DETACHED_TOOL_NAME_DISPLAY_LENGTH),
        "message": {
            "title": shorten(message.title, DETACHED_TITLE_DISPLAY_LENGTH),
            "body": shorten(message.body, DETACHED_BODY_DISPLAY_LENGTH),
            "speech": shorten(message.speech, DETACHED_SPEECH_DISPLAY_LENGTH),
            "urgent": message.urgent,
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
        send_message(message)
        log_backend_success(
            {
                "hook_event_name": payload.get("event", ""),
                "tool_name": payload.get("tool_name", ""),
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


def tool_detail(tool_input) -> str:
    if isinstance(tool_input, dict):
        desc = tool_input.get("description")
        command = tool_input.get("command")
        if desc:
            return "用途：已收到"
        if command:
            return "参数：已收到"
        return "参数：已收到"
    if tool_input:
        return "参数：已收到"
    return ""


def agent_name(data: dict) -> str:
    return shorten(
        data.get("agent_type")
        or data.get("agent_id")
        or data.get("subagent_type")
        or data.get("subagent_id")
        or "未知 subagent",
        AGENT_NAME_DISPLAY_LENGTH,
    )


def has_agent_context(data: dict) -> bool:
    return any(data.get(key) for key in ("agent_type", "agent_id", "subagent_type", "subagent_id"))


def is_agent_completion_event(data: dict) -> bool:
    event = data.get("hook_event_name", "")
    return event == "SubagentStop"


def uses_json_stdout(data: dict) -> bool:
    return data.get("hook_event_name", "") in ("Stop", "SubagentStop")


def write_json_stdout() -> None:
    # Stop/SubagentStop 必须给 Codex 返回合法 JSON；通知和语音走脱离 hook 的子进程。
    sys.stdout.write("{}")
    sys.stdout.flush()


def agent_context_lines(data: dict) -> list[str]:
    lines = [f"角色：{agent_name(data)}"]
    agent_id = short_id(data.get("agent_id") or data.get("subagent_id"), AGENT_ID_DISPLAY_LENGTH)
    if agent_id:
        lines.append(f"Agent：{agent_id}")
    return lines


def build_message(data: dict) -> HookMessage | None:
    event = data.get("hook_event_name", "")

    if event == "UserPromptSubmit":
        return HookMessage(
            title="Codex 任务已开始",
            body=body_text(["正在处理你的请求。"], data),
            speech="Codex has started your task.",
        )

    if event == "Stop":
        return HookMessage(
            title="Codex 任务已结束",
            body=body_text(["可以回到 Codex 查看结果。"], data),
            speech="Codex task is finished.",
        )

    if event == "SubagentStart":
        agent = agent_name(data)
        lines = agent_context_lines(data)
        lines.append("正在处理分配给它的工作。")
        return HookMessage(
            title="Codex 子任务已启动",
            body=body_text(lines, data),
            speech=f"Codex subagent {agent} has started.",
        )

    if event == "PermissionRequest":
        tool = data.get("tool_name") or "未知工具"
        detail = tool_detail(data.get("tool_input"))
        lines = ["需要你回到 Codex 处理权限弹窗。", f"工具：{tool}"]
        if detail:
            lines.append(detail)
        return HookMessage(
            title="Codex 等你确认权限",
            body=body_text(lines, data),
            speech="Codex is waiting for your permission.",
            urgent=True,
        )

    if is_agent_completion_event(data):
        agent = agent_name(data)
        lines = agent_context_lines(data)
        lines.append("可以回到 Codex 查看结果。")
        return HookMessage(
            title="Codex 子任务已完成",
            body=body_text(lines, data),
            speech=f"Codex subagent {agent} is finished.",
        )

    return None


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == DETACHED_NOTIFY_ARG:
        return run_detached_notification()

    data = read_hook_input()
    event = data.get("hook_event_name", "")
    wrote_json_stdout = False

    try:
        if uses_json_stdout(data):
            message = build_message(data)
            write_json_stdout()
            wrote_json_stdout = True
            if message:
                send_message_detached(data, message)
            return 0

        message = build_message(data)
        if message:
            send_message(message)
            log_backend_success(data, message)
        else:
            log_fallback(data, "no_message_for_event")

    except Exception as exc:
        log_error("main", exc, {"hook_event_name": event})
        if uses_json_stdout(data) and not wrote_json_stdout:
            write_json_stdout()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
