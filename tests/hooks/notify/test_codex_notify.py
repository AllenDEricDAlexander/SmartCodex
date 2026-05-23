import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "hooks" / "notify" / "codex_notify.py"
ROOT_SCRIPT_PATH = ROOT / "codex_notify.py"


def load_notify_module():
    spec = importlib.util.spec_from_file_location("codex_notify", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class NotifyScriptLayoutTest(unittest.TestCase):
    def test_notify_script_lives_under_hooks_notify(self):
        self.assertTrue(
            SCRIPT_PATH.exists(),
            "notify script should live under hooks/notify/codex_notify.py",
        )

    def test_root_compatibility_entrypoint_exists(self):
        self.assertTrue(
            ROOT_SCRIPT_PATH.exists(),
            "root codex_notify.py should remain as a compatibility entrypoint",
        )


class CodexNotifyMessageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SCRIPT_PATH.exists():
            raise unittest.SkipTest("notify script has not been moved yet")
        cls.notify = load_notify_module()

    def test_permission_request_message_is_actionable(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "PermissionRequest",
                "tool_name": "functions.exec_command",
                "tool_input": {
                    "description": "Run frontend dev server",
                    "command": "npm run dev",
                },
            }
        )

        self.assertEqual("Codex 等你确认权限", message.title)
        self.assertIn("需要你回到 Codex 处理权限弹窗。", message.body)
        self.assertIn("工具：functions.exec_command", message.body)
        self.assertIn("用途：已收到", message.body)
        self.assertEqual("Codex is waiting for your permission.", message.speech)
        self.assertTrue(message.urgent)

    def test_permission_request_message_does_not_expose_command_or_description(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "PermissionRequest",
                "tool_name": "functions.exec_command",
                "tool_input": {
                    "description": "Deploy production --token super-secret-token",
                    "command": "deploy --token super-secret-token",
                },
            }
        )

        text = "\n".join((message.title, message.body, message.speech))
        self.assertIn("用途：已收到", message.body)
        self.assertNotIn("super-secret-token", text)
        self.assertNotIn("Deploy production", text)
        self.assertNotIn("deploy --token", text)

    def test_permission_request_message_does_not_expose_command_when_description_missing(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "PermissionRequest",
                "tool_name": "functions.exec_command",
                "tool_input": {
                    "command": "deploy --token super-secret-token",
                },
            }
        )

        text = "\n".join((message.title, message.body, message.speech))
        self.assertIn("参数：已收到", message.body)
        self.assertNotIn("super-secret-token", text)
        self.assertNotIn("deploy --token", text)

    def test_user_prompt_submit_message_marks_parent_task_start(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "UserPromptSubmit",
                "prompt": "write the full secret customer migration prompt",
                "cwd": "/Users/mario/SelfProject/SmartCodex",
                "session_id": "session-1234567890abcdef",
                "turn_id": "turn-abcdef1234567890",
            }
        )

        self.assertEqual("Codex 任务已开始", message.title)
        self.assertIn("项目：SmartCodex", message.body)
        self.assertIn("会话：session-1234", message.body)
        self.assertIn("回合：turn-abcdef", message.body)
        self.assertIn("正在处理你的请求。", message.body)
        self.assertEqual("Codex has started your task.", message.speech)
        self.assertNotIn("write the full secret customer migration prompt", message.body)
        self.assertFalse(message.urgent)

    def test_parent_stop_message_marks_parent_task_end(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "Stop",
                "cwd": "/Users/mario/SelfProject/SmartCodex",
                "session_id": "session-1234567890abcdef",
                "turn_id": "turn-abcdef1234567890",
                "last_assistant_message": "full assistant answer that should stay private",
            }
        )

        self.assertEqual("Codex 任务已结束", message.title)
        self.assertIn("项目：SmartCodex", message.body)
        self.assertIn("会话：session-1234", message.body)
        self.assertIn("回合：turn-abcdef", message.body)
        self.assertIn("可以回到 Codex 查看结果。", message.body)
        self.assertEqual("Codex task is finished.", message.speech)
        self.assertNotIn("full assistant answer that should stay private", message.body)
        self.assertFalse(message.urgent)

    def test_parent_stop_without_agent_fields_has_message(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "Stop",
                "cwd": "/Users/mario/SelfProject/SmartCodex",
            }
        )

        self.assertIsNotNone(message)
        self.assertEqual("Codex 任务已结束", message.title)

    def test_subagent_start_message_names_agent_and_next_state(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "SubagentStart",
                "agent_type": "code-review",
            }
        )

        self.assertEqual("Codex 子任务已启动", message.title)
        self.assertEqual("角色：code-review\n正在处理分配给它的工作。", message.body)
        self.assertEqual("Codex subagent code-review has started.", message.speech)
        self.assertFalse(message.urgent)

    def test_subagent_start_message_includes_safe_context_when_present(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "SubagentStart",
                "agent_id": "agent-1234567890",
                "agent_type": "code-review",
                "cwd": "/Users/mario/SelfProject/SmartCodex",
                "session_id": "session-1234567890abcdef",
                "turn_id": "turn-abcdef1234567890",
            }
        )

        self.assertIn("角色：code-review", message.body)
        self.assertIn("Agent：agent-123456", message.body)
        self.assertIn("项目：SmartCodex", message.body)
        self.assertIn("会话：session-1234", message.body)
        self.assertIn("回合：turn-abcdef", message.body)

    def test_subagent_stop_message_invites_return_to_codex(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "SubagentStop",
                "agent_type": "code-review",
            }
        )

        self.assertEqual("Codex 子任务已完成", message.title)
        self.assertEqual("角色：code-review\n可以回到 Codex 查看结果。", message.body)
        self.assertEqual("Codex subagent code-review is finished.", message.speech)
        self.assertFalse(message.urgent)

    def test_subagent_stop_message_uses_subagent_completion_fields(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "SubagentStop",
                "agent_id": "agent-1234567890",
                "agent_type": "fullstack-developer",
                "turn_id": "turn-abcdef1234567890",
                "cwd": "/Users/mario/SelfProject/SmartCodex",
                "last_assistant_message": "full subagent result that should stay private",
            }
        )

        self.assertEqual("Codex 子任务已完成", message.title)
        self.assertIn("角色：fullstack-developer", message.body)
        self.assertIn("Agent：agent-123456", message.body)
        self.assertIn("回合：turn-abcdef", message.body)
        self.assertIn("可以回到 Codex 查看结果。", message.body)
        self.assertNotIn("full subagent result that should stay private", message.body)
        self.assertEqual("Codex subagent fullstack-developer is finished.", message.speech)
        self.assertFalse(message.urgent)

    def test_subagent_stop_message_bounds_long_agent_project_and_thread_context(self):
        long_agent = "fullstack-" + ("agent" * 200)
        long_project = "SmartCodex-" + ("Project" * 200)
        long_session = "session-" + ("1234567890" * 100)
        long_turn = "turn-" + ("abcdef1234567890" * 100)

        message = self.notify.build_message(
            {
                "hook_event_name": "SubagentStop",
                "agent_id": "agent-1234567890",
                "agent_type": long_agent,
                "cwd": f"/Users/mario/SelfProject/{long_project}",
                "session_id": long_session,
                "turn_id": long_turn,
            }
        )

        self.assertIn("角色：fullstack-", message.body)
        self.assertIn("项目：SmartCodex-", message.body)
        self.assertIn("会话：session-1234", message.body)
        self.assertIn("回合：turn-abcdef", message.body)
        self.assertLess(len(message.body), 260)
        self.assertLess(len(message.speech), 120)
        self.assertNotIn("agent" * 50, message.body)
        self.assertNotIn("Project" * 50, message.body)
        self.assertNotIn("1234567890" * 10, message.body)
        self.assertNotIn("abcdef1234567890" * 10, message.body)
        self.assertNotIn("agent" * 50, message.speech)

    def test_subagent_stop_completion_path_is_detected(self):
        self.assertTrue(
            self.notify.is_agent_completion_event(
                {
                    "hook_event_name": "SubagentStop",
                    "agent_type": "code-review",
                }
            )
        )

    def test_agent_stop_is_not_required_for_completion_detection(self):
        self.assertFalse(
            self.notify.is_agent_completion_event(
                {
                    "hook_event_name": "AgentStop",
                    "agent_type": "code-review",
                }
            )
        )

    def test_permission_request_context_excludes_full_tool_input(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "PermissionRequest",
                "tool_name": "functions.exec_command",
                "tool_input": {
                    "unknown": "full secret tool input should not appear",
                },
                "cwd": "/Users/mario/SelfProject/SmartCodex",
                "session_id": "session-1234567890abcdef",
                "turn_id": "turn-abcdef1234567890",
            }
        )

        self.assertIn("项目：SmartCodex", message.body)
        self.assertIn("会话：session-1234", message.body)
        self.assertIn("回合：turn-abcdef", message.body)
        self.assertNotIn("full secret tool input should not appear", message.body)

    def test_notification_text_excludes_sensitive_hook_fields(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "UserPromptSubmit",
                "prompt": "full prompt should not appear",
                "last_assistant_message": "full last assistant message should not appear",
                "tool_input": {
                    "unknown": "full tool input should not appear",
                },
                "transcript": "transcript content should not appear",
                "cwd": "/Users/mario/SelfProject/SmartCodex",
                "session_id": "session-1234567890abcdef",
                "turn_id": "turn-abcdef1234567890",
            }
        )

        text = "\n".join((message.title, message.body, message.speech))
        self.assertNotIn("full prompt should not appear", text)
        self.assertNotIn("full last assistant message should not appear", text)
        self.assertNotIn("full tool input should not appear", text)
        self.assertNotIn("transcript content should not appear", text)


class CodexNotifyStdoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SCRIPT_PATH.exists():
            raise unittest.SkipTest("notify script has not been moved yet")
        cls.notify = load_notify_module()

    def run_main_for_stdout(self, payload):
        with mock.patch.object(self.notify, "read_hook_input", return_value=payload), \
                mock.patch.object(self.notify, "send_message"), \
                mock.patch.object(self.notify, "run_detached"), \
                mock.patch.object(self.notify, "log_backend_success"), \
                mock.patch.object(sys, "stdout") as stdout:
            exit_code = self.notify.main()

        return exit_code, "".join(call.args[0] for call in stdout.write.call_args_list)

    def assert_empty_or_json_stdout(self, stdout):
        if stdout:
            json.loads(stdout)

    def run_stop_with_detached_tracking(self, payload):
        events = []

        class RecordingStdout:
            def write(self, text):
                events.append(("write", text))

            def flush(self):
                events.append(("flush",))

        def blocking_send_message(message):
            events.append(("send_message", message.title))

        def recording_run_detached(cmd, **kwargs):
            events.append(("run_detached", cmd, kwargs.get("env", {})))

        with mock.patch.object(self.notify, "read_hook_input", return_value=payload), \
                mock.patch.object(self.notify, "send_message", side_effect=blocking_send_message), \
                mock.patch.object(self.notify, "run_detached", side_effect=recording_run_detached), \
                mock.patch.object(sys, "stdout", RecordingStdout()):
            exit_code = self.notify.main()

        return exit_code, events

    def test_stop_stdout_is_empty_or_valid_json(self):
        exit_code, stdout = self.run_main_for_stdout(
            {
                "hook_event_name": "Stop",
                "cwd": "/Users/mario/SelfProject/SmartCodex",
            }
        )

        self.assertEqual(0, exit_code)
        self.assert_empty_or_json_stdout(stdout)
        self.assertNotEqual("no_message_for_event", stdout)

    def test_subagent_stop_stdout_is_empty_or_valid_json(self):
        exit_code, stdout = self.run_main_for_stdout(
            {
                "hook_event_name": "SubagentStop",
                "agent_id": "agent-1234567890",
                "agent_type": "code-review",
            }
        )

        self.assertEqual(0, exit_code)
        self.assert_empty_or_json_stdout(stdout)
        self.assertNotEqual("no_message_for_event", stdout)

    def test_stop_returns_after_stdout_without_sync_send_message(self):
        exit_code, events = self.run_stop_with_detached_tracking(
            {
                "hook_event_name": "Stop",
                "cwd": "/Users/mario/SelfProject/SmartCodex",
            }
        )

        self.assertEqual(0, exit_code)
        self.assertEqual(("write", "{}"), events[0])
        self.assertEqual(("flush",), events[1])
        self.assertFalse(any(event[0] == "send_message" for event in events))

    def test_subagent_stop_returns_after_stdout_without_sync_send_message(self):
        exit_code, events = self.run_stop_with_detached_tracking(
            {
                "hook_event_name": "SubagentStop",
                "agent_id": "agent-1234567890",
                "agent_type": "code-review",
            }
        )

        self.assertEqual(0, exit_code)
        self.assertEqual(("write", "{}"), events[0])
        self.assertEqual(("flush",), events[1])
        self.assertFalse(any(event[0] == "send_message" for event in events))

    def test_stop_invokes_detached_notification_with_expected_event_and_message(self):
        exit_code, events = self.run_stop_with_detached_tracking(
            {
                "hook_event_name": "Stop",
                "cwd": "/Users/mario/SelfProject/SmartCodex",
            }
        )

        detached_events = [event for event in events if event[0] == "run_detached"]
        self.assertEqual(0, exit_code)
        self.assertEqual(1, len(detached_events))
        self.assertIn(self.notify.DETACHED_NOTIFY_ARG, detached_events[0][1])
        payload = json.loads(detached_events[0][2][self.notify.DETACHED_NOTIFY_ENV])
        self.assertEqual("Stop", payload["event"])
        self.assertEqual("Codex 任务已结束", payload["message"]["title"])

    def test_subagent_stop_invokes_detached_notification_with_expected_event_and_message(self):
        exit_code, events = self.run_stop_with_detached_tracking(
            {
                "hook_event_name": "SubagentStop",
                "agent_id": "agent-1234567890",
                "agent_type": "code-review",
            }
        )

        detached_events = [event for event in events if event[0] == "run_detached"]
        self.assertEqual(0, exit_code)
        self.assertEqual(1, len(detached_events))
        self.assertIn(self.notify.DETACHED_NOTIFY_ARG, detached_events[0][1])
        payload = json.loads(detached_events[0][2][self.notify.DETACHED_NOTIFY_ENV])
        self.assertEqual("SubagentStop", payload["event"])
        self.assertEqual("Codex 子任务已完成", payload["message"]["title"])
        self.assertEqual("Codex subagent code-review is finished.", payload["message"]["speech"])

    def test_subagent_stop_detached_payload_is_bounded_with_long_context(self):
        long_agent = "fullstack-" + ("agent" * 400)
        long_project = "SmartCodex-" + ("Project" * 400)
        long_session = "session-" + ("1234567890" * 200)
        long_turn = "turn-" + ("abcdef1234567890" * 200)

        exit_code, events = self.run_stop_with_detached_tracking(
            {
                "hook_event_name": "SubagentStop",
                "agent_id": "agent-1234567890",
                "agent_type": long_agent,
                "cwd": f"/Users/mario/SelfProject/{long_project}",
                "session_id": long_session,
                "turn_id": long_turn,
            }
        )

        detached_events = [event for event in events if event[0] == "run_detached"]
        self.assertEqual(0, exit_code)
        self.assertEqual(1, len(detached_events))
        raw_payload = detached_events[0][2][self.notify.DETACHED_NOTIFY_ENV]
        payload = json.loads(raw_payload)
        text = json.dumps(payload["message"], ensure_ascii=False)

        self.assertLess(len(raw_payload), 900)
        self.assertIn("角色：fullstack-", payload["message"]["body"])
        self.assertIn("项目：SmartCodex-", payload["message"]["body"])
        self.assertIn("会话：session-1234", payload["message"]["body"])
        self.assertIn("回合：turn-abcdef", payload["message"]["body"])
        self.assertNotIn("agent" * 50, text)
        self.assertNotIn("Project" * 50, text)
        self.assertNotIn("1234567890" * 10, text)
        self.assertNotIn("abcdef1234567890" * 10, text)

    def test_stop_detached_payload_bounds_long_tool_name(self):
        long_tool_name = "functions." + ("exec_command" * 10000)

        exit_code, events = self.run_stop_with_detached_tracking(
            {
                "hook_event_name": "Stop",
                "tool_name": long_tool_name,
                "cwd": "/Users/mario/SelfProject/SmartCodex",
            }
        )

        detached_events = [event for event in events if event[0] == "run_detached"]
        self.assertEqual(0, exit_code)
        self.assertEqual(1, len(detached_events))
        raw_payload = detached_events[0][2][self.notify.DETACHED_NOTIFY_ENV]
        payload = json.loads(raw_payload)

        self.assertLess(len(raw_payload), 1200)
        self.assertLessEqual(len(payload["tool_name"]), self.notify.DETACHED_TOOL_NAME_DISPLAY_LENGTH)
        self.assertNotIn("exec_command" * 100, raw_payload)

    def test_detached_notification_entrypoint_invokes_notify_behavior(self):
        calls = []
        payload = {
            "event": "Stop",
            "tool_name": "",
            "message": {
                "title": "Codex 任务已结束",
                "body": "可以回到 Codex 查看结果。",
                "speech": "Codex task is finished.",
                "urgent": False,
            },
        }

        def recording_send_message(message):
            calls.append(("send_message", message.title, message.speech))

        def recording_log_backend_success(data, message):
            calls.append(("log_backend_success", data["hook_event_name"], message.title))

        with mock.patch.dict(os.environ, {self.notify.DETACHED_NOTIFY_ENV: json.dumps(payload)}, clear=True), \
                mock.patch.object(self.notify, "send_message", side_effect=recording_send_message), \
                mock.patch.object(self.notify, "log_backend_success", side_effect=recording_log_backend_success):
            exit_code = self.notify.run_detached_notification()

        self.assertEqual(0, exit_code)
        self.assertEqual(
            [
                ("send_message", "Codex 任务已结束", "Codex task is finished."),
                ("log_backend_success", "Stop", "Codex 任务已结束"),
            ],
            calls,
        )


class CodexNotifySpeechTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SCRIPT_PATH.exists():
            raise unittest.SkipTest("notify script has not been moved yet")
        cls.notify = load_notify_module()

    def test_concurrent_speech_requests_do_not_overlap(self):
        active = 0
        max_active = 0
        lock = threading.Lock()

        class FakeProcess:
            def __init__(self, cmd, **kwargs):
                nonlocal active, max_active
                self.cmd = cmd
                with lock:
                    active += 1
                    max_active = max(max_active, active)

            def wait(self, timeout=None):
                nonlocal active
                with lock:
                    active -= 1
                return 0

        with tempfile.TemporaryDirectory() as tmpdir:
            old_log_path = self.notify.LOG_PATH
            try:
                self.notify.LOG_PATH = Path(tmpdir) / "codex_notify.jsonl"
                with mock.patch.object(self.notify.platform, "system", return_value="Darwin"), \
                        mock.patch.object(self.notify.shutil, "which", return_value="/usr/bin/say"), \
                        mock.patch.object(self.notify.subprocess, "Popen", side_effect=FakeProcess):
                    threads = [
                        threading.Thread(target=self.notify.speak, args=(f"message {index}",))
                        for index in range(2)
                    ]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join(timeout=2)
            finally:
                self.notify.LOG_PATH = old_log_path

        self.assertEqual(1, max_active)

    def test_multiple_speech_requests_are_serialized(self):
        calls = []
        lock = threading.Lock()

        class FakeProcess:
            def __init__(self, cmd, **kwargs):
                self.cmd = cmd
                with lock:
                    calls.append(("start", cmd[-1]))

            def wait(self, timeout=None):
                with lock:
                    calls.append(("finish", self.cmd[-1]))
                return 0

        with tempfile.TemporaryDirectory() as tmpdir:
            old_log_path = self.notify.LOG_PATH
            try:
                self.notify.LOG_PATH = Path(tmpdir) / "codex_notify.jsonl"
                with mock.patch.object(self.notify.platform, "system", return_value="Darwin"), \
                        mock.patch.object(self.notify.shutil, "which", return_value="/usr/bin/say"), \
                        mock.patch.object(self.notify.subprocess, "Popen", side_effect=FakeProcess):
                    threads = [
                        threading.Thread(target=self.notify.speak, args=(text,))
                        for text in ("first", "second")
                    ]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join(timeout=2)
            finally:
                self.notify.LOG_PATH = old_log_path

        self.assertIn(
            calls,
            (
                [("start", "first"), ("finish", "first"), ("start", "second"), ("finish", "second")],
                [("start", "second"), ("finish", "second"), ("start", "first"), ("finish", "first")],
            ),
        )

    def test_speech_timeout_terminates_child_process(self):
        calls = []

        class FakeProcess:
            def __init__(self, cmd, **kwargs):
                calls.append(("start", cmd))

            def wait(self, timeout=None):
                calls.append(("wait", timeout))
                if len([call for call in calls if call[0] == "wait"]) == 1:
                    raise self_notify.subprocess.TimeoutExpired(["say", "slow"], timeout)
                return 0

            def terminate(self):
                calls.append(("terminate",))

            def kill(self):
                calls.append(("kill",))

        self_notify = self.notify

        with tempfile.TemporaryDirectory() as tmpdir:
            old_log_path = self.notify.LOG_PATH
            try:
                self.notify.LOG_PATH = Path(tmpdir) / "codex_notify.jsonl"
                with mock.patch.object(self.notify.subprocess, "Popen", side_effect=FakeProcess):
                    self.notify.run_speech(["say", "slow"])
            finally:
                self.notify.LOG_PATH = old_log_path

        self.assertEqual(("start", ["say", "slow"]), calls[0])
        self.assertIn(("wait", self.notify.SPEECH_WAIT_TIMEOUT_SECONDS), calls)
        self.assertIn(("terminate",), calls)
        self.assertIn(("wait", self.notify.SPEECH_TERMINATE_TIMEOUT_SECONDS), calls)
        self.assertNotIn(("kill",), calls)

    def test_speech_timeout_kills_child_when_terminate_does_not_exit(self):
        calls = []

        class FakeProcess:
            def __init__(self, cmd, **kwargs):
                calls.append(("start", cmd))

            def wait(self, timeout=None):
                calls.append(("wait", timeout))
                wait_count = len([call for call in calls if call[0] == "wait"])
                if wait_count < 3:
                    raise self_notify.subprocess.TimeoutExpired(["say", "stuck"], timeout)
                return 0

            def terminate(self):
                calls.append(("terminate",))

            def kill(self):
                calls.append(("kill",))

        self_notify = self.notify

        with tempfile.TemporaryDirectory() as tmpdir:
            old_log_path = self.notify.LOG_PATH
            try:
                self.notify.LOG_PATH = Path(tmpdir) / "codex_notify.jsonl"
                with mock.patch.object(self.notify.subprocess, "Popen", side_effect=FakeProcess):
                    self.notify.run_speech(["say", "stuck"])
            finally:
                self.notify.LOG_PATH = old_log_path

        self.assertEqual(("start", ["say", "stuck"]), calls[0])
        self.assertIn(("wait", self.notify.SPEECH_WAIT_TIMEOUT_SECONDS), calls)
        self.assertIn(("terminate",), calls)
        self.assertIn(("wait", self.notify.SPEECH_TERMINATE_TIMEOUT_SECONDS), calls)
        self.assertIn(("kill",), calls)
        self.assertIn(("wait", self.notify.SPEECH_KILL_TIMEOUT_SECONDS), calls)


class CodexNotifyPermissionRequestNotifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SCRIPT_PATH.exists():
            raise unittest.SkipTest("notify script has not been moved yet")
        cls.notify = load_notify_module()

    def test_permission_request_uses_macos_notification_without_dialog_by_default(self):
        calls = []

        with mock.patch.object(self.notify.shutil, "which", return_value="/usr/bin/osascript"), \
                mock.patch.object(self.notify, "run_detached", side_effect=calls.append), \
                mock.patch.dict(os.environ, {}, clear=True):
            self.notify.notify_macos(
                "Codex 等你确认权限",
                "需要你回到 Codex 处理权限弹窗。",
                urgent=True,
            )

        scripts = "\n".join(call[-1] for call in calls)
        self.assertEqual(1, len(calls))
        self.assertIn("display notification", scripts)
        self.assertNotIn("display dialog", scripts)

    def test_permission_request_non_blocking_default_path_is_used_by_notify(self):
        calls = []

        with mock.patch.object(self.notify.platform, "system", return_value="Darwin"), \
                mock.patch.object(self.notify.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"), \
                mock.patch.object(self.notify, "run_detached", side_effect=calls.append), \
                mock.patch.object(self.notify, "run_speech"), \
                mock.patch.dict(os.environ, {}, clear=True):
            message = self.notify.build_message({"hook_event_name": "PermissionRequest"})
            self.notify.send_message(message)

        scripts = "\n".join(call[-1] for call in calls)
        self.assertEqual(1, len(calls))
        self.assertIn("display notification", scripts)
        self.assertNotIn("display dialog", scripts)

    def test_macos_permission_request_dialog_is_opt_in(self):
        calls = []

        with mock.patch.object(self.notify.shutil, "which", return_value="/usr/bin/osascript"), \
                mock.patch.object(self.notify, "run_detached", side_effect=calls.append), \
                mock.patch.dict(os.environ, {"CODEX_NOTIFY_URGENT_MODAL": "1"}, clear=True):
            self.notify.notify_macos(
                "Codex 等你确认权限",
                "需要你回到 Codex 处理权限弹窗。",
                urgent=True,
            )

        scripts = "\n".join(call[-1] for call in calls)
        self.assertEqual(2, len(calls))
        self.assertIn("display notification", scripts)
        self.assertIn("display dialog", scripts)


class CodexNotifyLoggingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SCRIPT_PATH.exists():
            raise unittest.SkipTest("notify script has not been moved yet")
        cls.notify = load_notify_module()

    def read_entries(self, log_path: Path) -> list[dict]:
        return [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_default_log_path_is_under_codex_logs_hooks_notify(self):
        self.assertEqual(
            Path.home() / ".codex" / "logs" / "hooks" / "notify" / "codex_notify.jsonl",
            self.notify.LOG_PATH,
        )

    def test_backend_success_is_logged_without_sensitive_tool_input(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "codex_notify.jsonl"
            old_log_path = self.notify.LOG_PATH
            try:
                self.notify.LOG_PATH = log_path
                self.notify.log_backend_success(
                    {
                        "hook_event_name": "PermissionRequest",
                        "tool_name": "functions.exec_command",
                        "tool_input": {
                            "description": "Deploy production",
                            "command": "deploy --token super-secret-token",
                        },
                    },
                    self.notify.HookMessage(
                        title="Codex 等你确认权限",
                        body="需要你回到 Codex 处理权限弹窗。",
                        speech="Codex is waiting for your permission.",
                        urgent=True,
                    ),
                )
            finally:
                self.notify.LOG_PATH = old_log_path

            entries = self.read_entries(log_path)

        self.assertEqual(1, len(entries))
        self.assertEqual("backend_success", entries[0]["type"])
        self.assertEqual("PermissionRequest", entries[0]["event"])
        self.assertEqual("functions.exec_command", entries[0]["tool_name"])
        self.assertEqual("Codex 等你确认权限", entries[0]["message"]["title"])
        self.assertTrue(entries[0]["message"]["urgent"])
        self.assertNotIn("tool_input", entries[0])
        self.assertNotIn("super-secret-token", json.dumps(entries[0], ensure_ascii=False))

    def test_parse_error_log_uses_safe_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "codex_notify.jsonl"
            old_log_path = self.notify.LOG_PATH
            try:
                self.notify.LOG_PATH = log_path
                self.notify.log_error(
                    "read_hook_input",
                    ValueError("bad json"),
                    raw='{"token":"super-secret-token","command":"deploy"}',
                )
            finally:
                self.notify.LOG_PATH = old_log_path

            entries = self.read_entries(log_path)

        self.assertEqual(1, len(entries))
        self.assertEqual("error", entries[0]["type"])
        self.assertEqual("read_hook_input", entries[0]["where"])
        self.assertEqual("ValueError", entries[0]["error_type"])
        self.assertNotIn("super-secret-token", json.dumps(entries[0], ensure_ascii=False))
        self.assertNotIn("deploy", json.dumps(entries[0], ensure_ascii=False))

    def test_fallback_path_is_logged_as_structured_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "codex_notify.jsonl"
            old_log_path = self.notify.LOG_PATH
            try:
                self.notify.LOG_PATH = log_path
                self.notify.log_fallback(
                    {
                        "hook_event_name": "UnknownEvent",
                        "tool_name": "functions.exec_command",
                        "tool_input": {
                            "command": "deploy --token super-secret-token",
                        },
                    },
                    "no_message_for_event",
                )
            finally:
                self.notify.LOG_PATH = old_log_path

            entries = self.read_entries(log_path)

        self.assertEqual(1, len(entries))
        self.assertEqual("fallback", entries[0]["type"])
        self.assertEqual("UnknownEvent", entries[0]["event"])
        self.assertEqual("functions.exec_command", entries[0]["tool_name"])
        self.assertEqual("no_message_for_event", entries[0]["reason"])
        self.assertNotIn("tool_input", entries[0])
        self.assertNotIn("super-secret-token", json.dumps(entries[0], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
