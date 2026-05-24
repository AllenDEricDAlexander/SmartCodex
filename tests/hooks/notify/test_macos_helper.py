import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
HELPER_PATH = ROOT / "hooks" / "notify" / "macos_helper" / "codex_macos_notify.py"
SWIFT_SOURCE_PATH = ROOT / "hooks" / "notify" / "macos_helper" / "CodexNotifyApp.swift"


def load_helper_module():
    spec = importlib.util.spec_from_file_location("codex_macos_notify", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MacosHelperStatusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_helper_module()

    def make_helper_dir(self, tmpdir):
        helper_dir = Path(tmpdir)
        resources_dir = helper_dir / self.helper.APP_NAME / "Contents" / "Resources"
        resources_dir.mkdir(parents=True)
        (resources_dir / "icon.icns").write_text("icon", encoding="utf-8")
        return helper_dir

    def test_open_helper_app_waits_for_status_file_ok(self):
        open_calls = []

        def fake_run(cmd, **kwargs):
            open_calls.append((cmd, kwargs))
            status_path = Path(cmd[cmd.index("--status-file") + 1])
            status_path.write_text("ok", encoding="utf-8")

            class Result:
                returncode = 0

            return Result()

        with tempfile.TemporaryDirectory() as tmpdir:
            helper_dir = self.make_helper_dir(tmpdir)
            with mock.patch.object(self.helper.shutil, "which", return_value="/usr/bin/open"), \
                    mock.patch.object(self.helper.subprocess, "run", side_effect=fake_run):
                result = self.helper.open_helper_app(helper_dir, ["--title", "Hello", "--message", "World"])

        self.assertTrue(result)
        self.assertNotIn("-W", open_calls[0][0])
        self.assertIn("--status-file", open_calls[0][0])

    def test_open_helper_app_fails_when_status_file_is_not_ok(self):
        def fake_run(cmd, **kwargs):
            status_path = Path(cmd[cmd.index("--status-file") + 1])
            status_path.write_text("error: denied", encoding="utf-8")

            class Result:
                returncode = 0

            return Result()

        with tempfile.TemporaryDirectory() as tmpdir:
            helper_dir = self.make_helper_dir(tmpdir)
            with mock.patch.object(self.helper.shutil, "which", return_value="/usr/bin/open"), \
                    mock.patch.object(self.helper.subprocess, "run", side_effect=fake_run):
                result = self.helper.open_helper_app(helper_dir, ["--title", "Hello", "--message", "World"])

        self.assertFalse(result)

    def test_swift_helper_reports_authorization_and_delivery_failures(self):
        source = SWIFT_SOURCE_PATH.read_text(encoding="utf-8")

        self.assertIn("--status-file", source)
        self.assertIn("authorizationGranted", source)
        self.assertIn("authorizationError", source)
        self.assertIn("deliveryError", source)
        self.assertIn("writeStatus(\"ok\")", source)


if __name__ == "__main__":
    unittest.main()
