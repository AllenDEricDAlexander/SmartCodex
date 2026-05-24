#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


APP_NAME = "CodexNotify.app"
APP_ICON_PATH = Path("Contents") / "Resources" / "icon.icns"


def osascript_display(title: str, message: str, subtitle: str = "", sound: str = "") -> int:
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    if subtitle:
        script += f" subtitle {json.dumps(subtitle)}"
    if sound:
        script += f" sound name {json.dumps(sound)}"
    return subprocess.run(["/usr/bin/osascript", "-e", script], check=False).returncode


def open_helper_app(helper_dir: Path, args: list[str]) -> bool:
    app_path = helper_dir / APP_NAME
    if not (app_path / APP_ICON_PATH).exists():
        return False
    open_bin = shutil.which("open")
    if not open_bin:
        return False
    applet_path = app_path / "Contents" / "MacOS" / "applet"
    status_path = Path(tempfile.gettempdir()) / f"smartcodex-notify-status-{id(args)}.txt"
    try:
        if status_path.exists():
            status_path.unlink()
        result = subprocess.run(
            [
                open_bin,
                "-g",
                "-n",
                str(app_path),
                "--args",
                *args,
                "--status-file",
                str(status_path),
            ],
            check=False,
            timeout=2,
        )
        if result.returncode != 0:
            return False
        if applet_path.exists():
            return True
        deadline = time.monotonic() + 4
        while not status_path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not status_path.exists():
            return False
        return status_path.read_text(encoding="utf-8").strip() == "ok"
    except Exception:
        return False
    finally:
        try:
            status_path.unlink()
        except OSError:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show a SmartCodex macOS notification.")
    parser.add_argument("--title", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--subtitle", default="")
    parser.add_argument("--sound", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(raw_args)
    helper_dir = Path(__file__).resolve().parent
    if open_helper_app(helper_dir, raw_args):
        return 0
    return osascript_display(args.title, args.message, args.subtitle, args.sound)


if __name__ == "__main__":
    raise SystemExit(main())
