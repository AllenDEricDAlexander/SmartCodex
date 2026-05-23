import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


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
        self.assertTrue(SCRIPT_PATH.exists())

    def test_root_compatibility_entrypoint_is_removed(self):
        self.assertFalse(ROOT_SCRIPT_PATH.exists())


class CodexNotifyMessageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SCRIPT_PATH.exists():
            raise unittest.SkipTest("notify script has not been moved yet")
        cls.notify = load_notify_module()

    def assert_visible_message(self, message, expected_display, expected_speech, urgent=False):
        self.assertEqual(expected_display, message.title)
        self.assertEqual(expected_display, message.body)
        self.assertEqual(expected_speech, message.speech)
        self.assertEqual(urgent, message.urgent)

    def assert_private_context_not_visible(self, text):
        forbidden = (
            "线程",
            "会话",
            "回合",
            "dialog",
            "thread",
            "session-",
            "turn-",
            "agent-123456",
            "550e8400-e29b-41d4-a716-446655440000",
            "super-secret-token",
            "full secret",
            "deploy --token",
        )
        for token in forbidden:
            self.assertNotIn(token, text)

    def test_session_start_message_is_english_parent_session_start(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "SessionStart",
                "source": "startup",
                "cwd": "/Users/mario/SelfProject/SmartReader",
            }
        )

        self.assert_visible_message(
            message,
            "SmartReader | parent | Session started",
            "Project SmartReader, parent agent, session started.",
        )

    def test_user_prompt_submit_message_is_english_parent_task_start(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "UserPromptSubmit",
                "prompt": "full secret prompt should not appear",
                "cwd": "/Users/mario/SelfProject/SmartReader",
                "session_id": "550e8400-e29b-41d4-a716-446655440000",
                "turn_id": "turn-abcdef1234567890",
            }
        )

        self.assert_visible_message(
            message,
            "SmartReader | parent | Task started",
            "Project SmartReader, parent agent, task started.",
        )
        self.assert_private_context_not_visible("\n".join((message.title, message.body, message.speech)))

    def test_permission_request_message_is_english_and_private(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "PermissionRequest",
                "tool_name": "functions.exec_command",
                "tool_input": {
                    "description": "Deploy production --token super-secret-token",
                    "command": "deploy --token super-secret-token",
                },
                "cwd": "/Users/mario/SelfProject/SmartReader",
                "session_id": "session-1234567890abcdef",
                "turn_id": "turn-abcdef1234567890",
            }
        )

        self.assert_visible_message(
            message,
            "SmartReader | parent | Permission requested",
            "Project SmartReader, parent agent, permission requested.",
            urgent=True,
        )
        self.assert_private_context_not_visible("\n".join((message.title, message.body, message.speech)))

    def test_parent_stop_message_is_english_parent_task_finished(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "Stop",
                "cwd": "/Users/mario/SelfProject/SmartReader",
                "last_assistant_message": "full secret assistant message",
                "turn_id": "turn-abcdef1234567890",
            }
        )

        self.assert_visible_message(
            message,
            "SmartReader | parent | Task finished",
            "Project SmartReader, parent agent, task finished.",
        )
        self.assert_private_context_not_visible("\n".join((message.title, message.body, message.speech)))

    def test_subagent_start_message_uses_agent_type(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "SubagentStart",
                "agent_id": "agent-1234567890",
                "agent_type": "fullstack-developer",
                "cwd": "/Users/mario/SelfProject/SmartReader",
                "turn_id": "turn-abcdef1234567890",
            }
        )

        self.assert_visible_message(
            message,
            "SmartReader | fullstack-developer | Task started",
            "Project SmartReader, fullstack developer agent, task started.",
        )
        self.assert_private_context_not_visible("\n".join((message.title, message.body, message.speech)))

    def test_subagent_stop_message_uses_agent_type(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "SubagentStop",
                "agent_id": "agent-1234567890",
                "agent_type": "code-reviewer",
                "cwd": "/Users/mario/SelfProject/SmartReader",
                "last_assistant_message": "full secret subagent result",
            }
        )

        self.assert_visible_message(
            message,
            "SmartReader | code-reviewer | Task finished",
            "Project SmartReader, code reviewer agent, task finished.",
        )
        self.assert_private_context_not_visible("\n".join((message.title, message.body, message.speech)))

    def test_subagent_message_uses_subagent_type_fallback(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "SubagentStart",
                "subagent_type": "qa-expert",
                "cwd": "/Users/mario/SelfProject/SmartReader",
            }
        )

        self.assert_visible_message(
            message,
            "SmartReader | qa-expert | Task started",
            "Project SmartReader, qa expert agent, task started.",
        )

    def test_subagent_message_does_not_use_uuid_agent_id_as_visible_agent(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "SubagentStart",
                "agent_id": "019e5398-bb2c-7921-ab11-a1c770ae1c37",
                "cwd": "/Users/mario/SelfProject/SmartReader",
            }
        )

        self.assert_visible_message(
            message,
            "SmartReader | subagent | Task started",
            "Project SmartReader, subagent, task started.",
        )
        self.assertNotIn("019e5398", "\n".join((message.title, message.body, message.speech)))

    def test_project_falls_back_to_process_cwd_when_payload_cwd_is_missing(self):
        with mock.patch.object(self.notify.Path, "cwd", return_value=Path("/Users/mario/SelfProject/SmartReader")), \
                mock.patch.dict(os.environ, {}, clear=True):
            message = self.notify.build_message({"hook_event_name": "UserPromptSubmit"})

        self.assert_visible_message(
            message,
            "SmartReader | parent | Task started",
            "Project SmartReader, parent agent, task started.",
        )

    def test_project_falls_back_to_pwd_when_process_cwd_is_unavailable(self):
        with mock.patch.object(self.notify.Path, "cwd", side_effect=OSError("cwd unavailable")), \
                mock.patch.dict(os.environ, {"PWD": "/Users/mario/SelfProject/SmartReader"}, clear=True):
            message = self.notify.build_message({"hook_event_name": "Stop"})

        self.assert_visible_message(
            message,
            "SmartReader | parent | Task finished",
            "Project SmartReader, parent agent, task finished.",
        )

    def test_unknown_project_is_used_only_after_all_project_sources_are_missing(self):
        with mock.patch.object(self.notify.Path, "cwd", side_effect=OSError("cwd unavailable")), \
                mock.patch.dict(os.environ, {}, clear=True):
            message = self.notify.build_message({"hook_event_name": "Stop"})

        self.assert_visible_message(
            message,
            "Unknown project | parent | Task finished",
            "Project unknown project, parent agent, task finished.",
        )

    def test_thread_and_conversation_names_are_not_visible(self):
        message = self.notify.build_message(
            {
                "hook_event_name": "UserPromptSubmit",
                "thread_name": "Secret Thread",
                "thread_title": "Secret Thread Title",
                "conversation_name": "Secret Conversation",
                "conversation_title": "Secret Conversation Title",
                "cwd": "/Users/mario/SelfProject/SmartReader",
            }
        )

        text = "\n".join((message.title, message.body, message.speech))
        self.assertIn("SmartReader | parent | Task started", message.title)
        self.assertNotIn("Secret Thread", text)
        self.assertNotIn("Secret Conversation", text)

    def test_registered_events_have_english_messages(self):
        cases = [
            ("SessionStart", "parent", "Session started", "parent agent", "session started"),
            ("UserPromptSubmit", "parent", "Task started", "parent agent", "task started"),
            ("PermissionRequest", "parent", "Permission requested", "parent agent", "permission requested"),
            ("SubagentStart", "fullstack-developer", "Task started", "fullstack developer agent", "task started"),
            ("SubagentStop", "fullstack-developer", "Task finished", "fullstack developer agent", "task finished"),
            ("Stop", "parent", "Task finished", "parent agent", "task finished"),
        ]

        for event, agent, action, speech_agent, speech_action in cases:
            with self.subTest(event=event):
                message = self.notify.build_message(
                    {
                        "hook_event_name": event,
                        "cwd": "/Users/mario/SelfProject/SmartReader",
                        "agent_type": "fullstack-developer",
                    }
                )

                self.assert_visible_message(
                    message,
                    f"SmartReader | {agent} | {action}",
                    f"Project SmartReader, {speech_agent}, {speech_action}.",
                    urgent=(event == "PermissionRequest"),
                )

    def test_long_project_and_agent_are_bounded(self):
        long_project = "SmartReader-" + ("Project" * 200)
        long_agent = "fullstack-" + ("agent" * 200)
        message = self.notify.build_message(
            {
                "hook_event_name": "SubagentStop",
                "cwd": f"/Users/mario/SelfProject/{long_project}",
                "agent_type": long_agent,
                "session_id": "session-" + ("1234567890" * 100),
                "turn_id": "turn-" + ("abcdef1234567890" * 100),
            }
        )

        text = "\n".join((message.title, message.body, message.speech))
        self.assertLessEqual(len(message.body), self.notify.VISIBLE_MESSAGE_DISPLAY_LENGTH)
        self.assertLessEqual(len(message.speech), self.notify.VISIBLE_MESSAGE_DISPLAY_LENGTH)
        self.assertIn("SmartReader-", message.body)
        self.assertIn("fullstack-", message.body)
        self.assertNotIn("Project" * 50, text)
        self.assertNotIn("agent" * 50, text)
        self.assert_private_context_not_visible(text)


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

    def run_stop_with_detached_tracking(self, payload):
        events = []

        class RecordingStdout:
            def write(self, text):
                events.append(("write", text))

            def flush(self):
                events.append(("flush",))

        def recording_run_detached(cmd, **kwargs):
            events.append(("run_detached", cmd, kwargs.get("env", {})))

        with mock.patch.object(self.notify, "read_hook_input", return_value=payload), \
                mock.patch.object(self.notify, "send_message"), \
                mock.patch.object(self.notify, "run_detached", side_effect=recording_run_detached), \
                mock.patch.object(sys, "stdout", RecordingStdout()):
            exit_code = self.notify.main()

        return exit_code, events

    def test_stop_stdout_is_valid_json_object(self):
        exit_code, stdout = self.run_main_for_stdout(
            {"hook_event_name": "Stop", "cwd": "/Users/mario/SelfProject/SmartReader"}
        )

        self.assertEqual(0, exit_code)
        self.assertEqual({}, json.loads(stdout))

    def test_subagent_stop_stdout_is_valid_json_object(self):
        exit_code, stdout = self.run_main_for_stdout(
            {"hook_event_name": "SubagentStop", "agent_type": "code-reviewer"}
        )

        self.assertEqual(0, exit_code)
        self.assertEqual({}, json.loads(stdout))

    def test_stop_returns_after_stdout_and_uses_detached_notification(self):
        exit_code, events = self.run_stop_with_detached_tracking(
            {"hook_event_name": "Stop", "cwd": "/Users/mario/SelfProject/SmartReader"}
        )

        self.assertEqual(0, exit_code)
        self.assertEqual(("write", "{}"), events[0])
        self.assertEqual(("flush",), events[1])
        detached_events = [event for event in events if event[0] == "run_detached"]
        self.assertEqual(1, len(detached_events))
        payload = json.loads(detached_events[0][2][self.notify.DETACHED_NOTIFY_ENV])
        self.assertEqual("Stop", payload["event"])
        self.assertEqual("SmartReader | parent | Task finished", payload["message"]["title"])
        self.assertEqual("Project SmartReader, parent agent, task finished.", payload["message"]["speech"])

    def test_subagent_stop_returns_after_stdout_and_uses_detached_notification(self):
        exit_code, events = self.run_stop_with_detached_tracking(
            {
                "hook_event_name": "SubagentStop",
                "agent_id": "agent-1234567890",
                "agent_type": "code-reviewer",
                "cwd": "/Users/mario/SelfProject/SmartReader",
            }
        )

        self.assertEqual(0, exit_code)
        self.assertEqual(("write", "{}"), events[0])
        self.assertEqual(("flush",), events[1])
        detached_events = [event for event in events if event[0] == "run_detached"]
        self.assertEqual(1, len(detached_events))
        payload = json.loads(detached_events[0][2][self.notify.DETACHED_NOTIFY_ENV])
        self.assertEqual("SubagentStop", payload["event"])
        self.assertEqual("SmartReader | code-reviewer | Task finished", payload["message"]["title"])
        self.assertEqual("Project SmartReader, code reviewer agent, task finished.", payload["message"]["speech"])

    def test_session_start_does_not_write_developer_context(self):
        exit_code, stdout = self.run_main_for_stdout(
            {"hook_event_name": "SessionStart", "cwd": "/Users/mario/SelfProject/SmartReader"}
        )

        self.assertEqual(0, exit_code)
        self.assertEqual("", stdout)


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
                    threads = [threading.Thread(target=self.notify.speak, args=(f"message {index}",)) for index in range(2)]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join(timeout=2)
            finally:
                self.notify.LOG_PATH = old_log_path

        self.assertEqual(1, max_active)

    def test_speech_waits_for_completion_without_timeout(self):
        calls = []

        class FakeProcess:
            def __init__(self, cmd, **kwargs):
                calls.append(("start", cmd))

            def wait(self, timeout=None):
                calls.append(("wait", timeout))
                return 0

            def terminate(self):
                calls.append(("terminate",))

            def kill(self):
                calls.append(("kill",))

        with tempfile.TemporaryDirectory() as tmpdir:
            old_log_path = self.notify.LOG_PATH
            try:
                self.notify.LOG_PATH = Path(tmpdir) / "codex_notify.jsonl"
                with mock.patch.object(self.notify.subprocess, "Popen", side_effect=FakeProcess):
                    self.notify.run_speech(["say", "slow"])
            finally:
                self.notify.LOG_PATH = old_log_path

        self.assertEqual(("start", ["say", "slow"]), calls[0])
        self.assertIn(("wait", None), calls)
        self.assertNotIn(("terminate",), calls)
        self.assertNotIn(("kill",), calls)

    def test_macos_say_uses_default_rate_300(self):
        calls = []

        with mock.patch.object(self.notify.platform, "system", return_value="Darwin"), \
                mock.patch.object(self.notify.shutil, "which", return_value="/usr/bin/say"), \
                mock.patch.object(self.notify, "run_speech", side_effect=calls.append), \
                mock.patch.dict(os.environ, {}, clear=True):
            self.notify.speak("hello")

        self.assertEqual([["say", "-r", "300", "hello"]], calls)

    def test_macos_say_rate_env_overrides_default(self):
        calls = []

        with mock.patch.object(self.notify.platform, "system", return_value="Darwin"), \
                mock.patch.object(self.notify.shutil, "which", return_value="/usr/bin/say"), \
                mock.patch.object(self.notify, "run_speech", side_effect=calls.append), \
                mock.patch.dict(os.environ, {"CODEX_NOTIFY_RATE": "240"}, clear=True):
            self.notify.speak("hello")

        self.assertEqual([["say", "-r", "240", "hello"]], calls)


class CodexNotifyMacNotificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SCRIPT_PATH.exists():
            raise unittest.SkipTest("notify script has not been moved yet")
        cls.notify = load_notify_module()

    def test_macos_notification_uses_osascript_by_default_even_when_helper_exists(self):
        calls = []

        with tempfile.TemporaryDirectory() as tmpdir:
            helper = Path(tmpdir) / "codex_macos_notify.py"
            helper.write_text("# helper\n", encoding="utf-8")
            with mock.patch.object(self.notify.shutil, "which", return_value="/usr/bin/osascript"), \
                    mock.patch.object(self.notify, "run_detached", side_effect=calls.append), \
                    mock.patch.dict(os.environ, {"SMARTCODEX_NOTIFY_HELPER": str(helper)}, clear=True):
                self.notify.notify_macos("SmartReader | parent | Task started", "SmartReader | parent | Task started", urgent=False)

        scripts = "\n".join(call[-1] for call in calls)
        self.assertEqual(1, len(calls))
        self.assertIn("display notification", scripts)
        self.assertNotIn(str(helper), scripts)

    def test_macos_notification_helper_is_opt_in(self):
        calls = []

        with tempfile.TemporaryDirectory() as tmpdir:
            helper = Path(tmpdir) / "codex_macos_notify.py"
            helper.write_text("# helper\n", encoding="utf-8")
            with mock.patch.object(self.notify.shutil, "which", return_value="/usr/bin/osascript"), \
                    mock.patch.object(self.notify, "run_detached", side_effect=calls.append), \
                    mock.patch.dict(
                        os.environ,
                        {
                            "SMARTCODEX_NOTIFY_HELPER": str(helper),
                            "SMARTCODEX_NOTIFY_USE_HELPER": "1",
                        },
                        clear=True,
                    ):
                self.notify.notify_macos("SmartReader | parent | Task started", "SmartReader | parent | Task started", urgent=False)

        self.assertEqual(
            [
                [
                    sys.executable,
                    str(helper),
                    "--title",
                    "SmartReader | parent | Task started",
                    "--message",
                    "SmartReader | parent | Task started",
                ]
            ],
            calls,
        )

    def test_macos_permission_request_dialog_is_opt_in(self):
        calls = []

        with mock.patch.object(self.notify.shutil, "which", return_value="/usr/bin/osascript"), \
                mock.patch.object(self.notify, "run_detached", side_effect=calls.append), \
                mock.patch.dict(os.environ, {"CODEX_NOTIFY_URGENT_MODAL": "1"}, clear=True):
            self.notify.notify_macos("SmartReader | parent | Permission requested", "SmartReader | parent | Permission requested", urgent=True)

        scripts = "\n".join(call[-1] for call in calls)
        self.assertEqual(2, len(calls))
        self.assertIn("display notification", scripts)
        self.assertIn("display dialog", scripts)

    def test_permission_request_non_blocking_default_path_is_used_by_notify(self):
        calls = []

        with mock.patch.object(self.notify.platform, "system", return_value="Darwin"), \
                mock.patch.object(self.notify.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"), \
                mock.patch.object(self.notify, "run_detached", side_effect=calls.append), \
                mock.patch.object(self.notify, "run_speech"), \
                mock.patch.dict(os.environ, {}, clear=True):
            message = self.notify.build_message(
                {
                    "hook_event_name": "PermissionRequest",
                    "cwd": "/Users/mario/SelfProject/SmartReader",
                }
            )
            self.notify.send_message(message)

        scripts = "\n".join(call[-1] for call in calls)
        self.assertEqual(1, len(calls))
        self.assertIn("display notification", scripts)
        self.assertNotIn("display dialog", scripts)


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

    def test_backend_success_logs_safe_summary_and_resolution_sources(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "codex_notify.jsonl"
            old_log_path = self.notify.LOG_PATH
            try:
                self.notify.LOG_PATH = log_path
                self.notify.log_backend_success(
                    {
                        "hook_event_name": "PermissionRequest",
                        "tool_name": "functions.exec_command",
                        "tool_input": {"command": "deploy --token super-secret-token"},
                    },
                    self.notify.HookMessage(
                        title="SmartReader | parent | Permission requested",
                        body="SmartReader | parent | Permission requested",
                        speech="Project SmartReader, parent agent, permission requested.",
                        urgent=True,
                    ),
                    project_source="payload.cwd",
                    agent_source="parent_event",
                )
            finally:
                self.notify.LOG_PATH = old_log_path

            entries = self.read_entries(log_path)

        self.assertEqual(1, len(entries))
        self.assertEqual("backend_success", entries[0]["type"])
        self.assertEqual("PermissionRequest", entries[0]["event"])
        self.assertEqual("functions.exec_command", entries[0]["tool_name"])
        self.assertEqual("payload.cwd", entries[0]["project_source"])
        self.assertEqual("parent_event", entries[0]["agent_source"])
        self.assertEqual("SmartReader | parent | Permission requested", entries[0]["message"]["title"])
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
        self.assertNotIn("super-secret-token", json.dumps(entries[0], ensure_ascii=False))
        self.assertNotIn("deploy", json.dumps(entries[0], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
