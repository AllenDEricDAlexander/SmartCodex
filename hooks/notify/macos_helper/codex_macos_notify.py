#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys


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
    result = subprocess.run([open_bin, "-n", str(app_path), "--args", *args], check=False)
    return result.returncode == 0


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
