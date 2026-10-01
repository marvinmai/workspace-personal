"""Wire ai-personal into this machine's Claude Code (idempotent).

1. register the ai-personal marketplace and install/update the `personal` plugin
2. deploy rules/*.md to ~/.claude/rules/ (plugins cannot ship always-on rules)
3. merge settings/permissions.fragment.json into ~/.claude/settings.json
4. install the shell helpers
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config, shell

MARKETPLACE = "ai-personal"
PLUGIN = "personal"
RULE_MARK = "<!-- managed by ai-workspace link; source: ai-personal/rules -->"


def claude_dir() -> Path:
    return Path.home() / ".claude"


def _claude(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["claude", *args], capture_output=True, text=True)


def link_plugin(ai_dir: Path, dry_run: bool) -> str:
    if not shutil.which("claude"):
        return "WARN claude CLI not found: plugin not installed"
    if not (ai_dir / ".claude-plugin" / "marketplace.json").is_file():
        return f"FAIL no marketplace.json in {ai_dir} (clone ai-personal first)"
    listed = _claude("plugin", "marketplace", "list").stdout
    steps = []
    if MARKETPLACE not in listed:
        steps.append(["plugin", "marketplace", "add", str(ai_dir)])
    installed = f"{PLUGIN}@{MARKETPLACE}" in _claude("plugin", "list").stdout
    steps.append(["plugin", "update" if installed else "install", f"{PLUGIN}@{MARKETPLACE}"])
    if dry_run:
        return "would run: " + "; ".join("claude " + " ".join(s) for s in steps)
    for s in steps:
        res = _claude(*s)
        if res.returncode != 0:
            return f"FAIL claude {' '.join(s)}: {(res.stderr or res.stdout).strip()[:200]}"
    return f"OK plugin {PLUGIN}@{MARKETPLACE} {'updated' if installed else 'installed'}"


def link_rules(ai_dir: Path, dry_run: bool) -> list[str]:
    msgs = []
    dest_dir = claude_dir() / "rules"
    for src in sorted((ai_dir / "rules").glob("*.md")):
        want = RULE_MARK + "\n" + src.read_text(encoding="utf-8")
        dest = dest_dir / src.name
        if dest.exists():
            have = dest.read_text(encoding="utf-8")
            if have == want:
                msgs.append(f"OK rule {src.name} up to date")
                continue
            if not have.startswith(RULE_MARK):
                msgs.append(f"WARN rule {src.name}: unmanaged file exists, left untouched")
                continue
        if dry_run:
            msgs.append(f"would write rule {dest}")
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest.write_text(want, encoding="utf-8", newline="\n")
        msgs.append(f"OK rule {src.name} deployed")
    return msgs


def merge_permissions(settings: dict, fragment: dict) -> bool:
    """Union list entries of fragment['permissions'] into settings; True if changed."""
    changed = False
    perms = settings.setdefault("permissions", {})
    for key, values in (fragment.get("permissions") or {}).items():
        if not isinstance(values, list):
            continue
        current = perms.setdefault(key, [])
        for v in values:
            if v not in current:
                current.append(v)
                changed = True
    return changed


def link_settings(ai_dir: Path, dry_run: bool) -> str:
    frag_path = ai_dir / "settings" / "permissions.fragment.json"
    if not frag_path.is_file():
        return "WARN no settings fragment"
    fragment = json.loads(frag_path.read_text(encoding="utf-8"))
    path = claude_dir() / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
    if not merge_permissions(settings, fragment):
        return "OK settings permissions up to date"
    if dry_run:
        return f"would merge permissions into {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        shutil.copy2(path, path.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}"))
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return f"OK settings merged into {path} (backup kept)"


def wezterm_command(cfg: dict) -> list[str] | None:
    """Command that deploys the shared WezTerm config, or None when not applicable.

    Linux/macOS only, and only if WezTerm is installed: `link` never installs
    packages. On Windows the shell choice (wsl/pwsh/cmd) is an interactive decision,
    so run applications/wezterm/install_wezterm.py yourself there.
    """
    if sys.platform.startswith("win") or not shutil.which("wezterm"):
        return None
    script = config.WORKSPACE_ROOT / "applications" / "wezterm" / "install_wezterm.py"
    approach = cfg.get("wezterm_shell", "login")
    return [sys.executable, str(script), "--shell-approach", approach]


def link_wezterm(cfg: dict, dry_run: bool) -> str:
    cmd = wezterm_command(cfg)
    if cmd is None:
        return "OK wezterm not applicable here (not installed, or Windows: run its installer by hand)"
    if dry_run:
        return "would run: " + " ".join(cmd)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        return f"FAIL wezterm config: {(res.stderr or res.stdout).strip()[-200:]}"
    return "OK wezterm config deployed (restart WezTerm)"


def run(dry_run: bool = False) -> int:
    cfg = config.ensure_config()
    ai_dir = Path(cfg["ai_personal_dir"]).expanduser()
    lines = [link_plugin(ai_dir, dry_run), *link_rules(ai_dir, dry_run), link_settings(ai_dir, dry_run),
             link_wezterm(cfg, dry_run)]
    lines += [f"shell: {m}" for m in shell.install(dry_run)]
    for line in lines:
        print(line)
    return 1 if any(l.startswith("FAIL") for l in lines) else 0
