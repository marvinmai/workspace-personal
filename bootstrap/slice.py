"""`slice`: run the current repo's own launcher, scripts/slice.<ext>.

What a slice is and how it starts (issue picking, worktrees, sessions) is the
project's business; this only finds the launcher and picks the interpreter
from its extension, which also works on Windows, where shebangs don't.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


class SliceError(Exception):
    pass


def _powershell() -> list[str]:
    host = shutil.which("pwsh") or shutil.which("powershell") or "pwsh"
    return [host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]


INTERPRETERS = {
    ".mjs": lambda: ["node"],
    ".js": lambda: ["node"],
    ".cjs": lambda: ["node"],
    ".py": lambda: [sys.executable],
    ".sh": lambda: ["bash"],
    ".ps1": _powershell,
}


def command(path: Path, args: list[str]) -> list[str]:
    return INTERPRETERS[path.suffix.lower()]() + [str(path), *args]


def find_launcher(cwd: Path) -> Path:
    res = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd,
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise SliceError("slice: not in a git repository")
    root = Path(res.stdout.strip())
    found = sorted(p for p in (root / "scripts").glob("slice.*") if p.suffix.lower() in INTERPRETERS)
    if not found:
        raise SliceError(f"slice: {root} has no scripts/slice.<{','.join(s[1:] for s in INTERPRETERS)}>")
    if len(found) > 1:
        raise SliceError("slice: more than one launcher: " + ", ".join(p.name for p in found))
    return found[0]


def run(args: list[str]) -> int:
    try:
        launcher = find_launcher(Path.cwd())
    except SliceError as e:
        print(e, file=sys.stderr)
        return 1
    try:
        return subprocess.run(command(launcher, args)).returncode
    except FileNotFoundError as e:
        print(f"slice: interpreter not found: {e.filename}", file=sys.stderr)
        return 1
