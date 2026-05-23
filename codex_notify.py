#!/usr/bin/env python3
from __future__ import annotations

import runpy
from pathlib import Path


def main() -> int:
    script = Path(__file__).resolve().parent / "hooks" / "notify" / "codex_notify.py"
    namespace = runpy.run_path(str(script))
    return namespace["main"]()


if __name__ == "__main__":
    raise SystemExit(main())
