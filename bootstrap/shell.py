"""Shell helpers (ai-personal, notes, ai, clone, slice, ws-<command>) as a managed block.

POSIX: written into ~/.bashrc / ~/.zshrc between markers (idempotent).
Windows: delegated to scripts/setup-shell-extras.ps1, which owns the PowerShell
profile handling (encoding, markers, redirected profile paths).
Paths are resolved from the local config at call time, so changing the config
needs no re-link.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .config import WORKSPACE_ROOT

BEGIN = "# >>> ai-workspace >>>"
END = "# <<< ai-workspace <<<"
IS_WINDOWS = sys.platform.startswith("win")
# bootstrap subcommands that get a `ws-<command>` shortcut
SHORT_COMMANDS = ("link", "doctor", "setup", "config")


def posix_block() -> str:
    ws = str(WORKSPACE_ROOT)
    return "\n".join([
        BEGIN,
        "# Managed by workspace-personal (ai-workspace link). Edit the config, not this block.",
        f'_aiw() {{ PYTHONPATH="{ws}" "$(command -v python3 || command -v python)" -m bootstrap "$@"; }}',
        'ai-personal() { cd "$(_aiw config get ai_personal_dir)" || return; }',
        'notes() { cd "$(_aiw config get notes_dir)" || return; }',
        'ai() { claude "$@"; }',
        'clone() { _aiw clone "$@"; }',
        'slice() { _aiw slice "$@"; }',
        *(f'ws-{c}() {{ _aiw {c} "$@"; }}' for c in SHORT_COMMANDS),
        END,
    ]) + "\n"


def replace_block(text: str, block: str) -> str:
    if BEGIN in text:
        start = text.index(BEGIN)
        end = text.find(END, start)
        if end < 0:
            raise ValueError("malformed ai-workspace block (end marker missing)")
        return text[:start] + block.rstrip("\n") + text[end + len(END):]
    sep = "" if (not text or text.endswith("\n")) else "\n"
    return text + sep + ("\n" if text else "") + block


def install_posix(dry_run: bool = False) -> list[str]:
    msgs = []
    targets = [p for p in (Path.home() / ".bashrc", Path.home() / ".zshrc") if p.exists()]
    if not targets:
        return ["no ~/.bashrc or ~/.zshrc found: add the block from `ai-workspace link --dry-run` manually"]
    for rc in targets:
        old = rc.read_text(encoding="utf-8")
        new = replace_block(old, posix_block())
        if new == old:
            msgs.append(f"{rc}: up to date")
        elif dry_run:
            msgs.append(f"{rc}: would update")
        else:
            rc.write_text(new, encoding="utf-8", newline="\n")
            msgs.append(f"{rc}: updated (open a new shell)")
    return msgs


def install_windows(dry_run: bool = False) -> list[str]:
    script = WORKSPACE_ROOT / "scripts" / "setup-shell-extras.ps1"
    host = shutil.which("pwsh") or shutil.which("powershell")
    if not host:
        return ["PowerShell not found: skipped profile helpers"]
    if dry_run:
        return [f"would run {script}"]
    res = subprocess.run([host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Quiet"])
    return ["PowerShell profile helpers installed" if res.returncode == 0
            else f"setup-shell-extras.ps1 failed (exit {res.returncode})"]


def install(dry_run: bool = False) -> list[str]:
    return install_windows(dry_run) if IS_WINDOWS else install_posix(dry_run)
