"""Per-machine configuration.

Stored outside every repo so nothing machine- or employer-specific is committed:
  Windows: %APPDATA%\\ai-workspace\\config.json
  other:   $XDG_CONFIG_HOME/ai-workspace/config.json (default ~/.config/...)

Keys:
  repos_dir        parent folder for cloned repositories
  ai_personal_dir  checkout of the ai-personal plugin repo
  notes_dir        the notes vault (Obsidian vault + git repo)
  sources          list of {"type": "github", "owner": "<user-or-org>"}
  wezterm_shell    "login" (default; your $SHELL) or "pwsh"  (Linux/macOS)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent


def config_dir() -> Path:
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "ai-workspace"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ai-workspace"


def config_path() -> Path:
    return config_dir() / "config.json"


def _origin_owner(repo: Path) -> str | None:
    """GitHub owner taken from this workspace repo's own `origin` remote."""
    try:
        url = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        return None
    for prefix in ("git@github.com:", "https://github.com/", "ssh://git@github.com/"):
        if url.startswith(prefix):
            return url[len(prefix):].split("/")[0] or None
    return None


def default_config() -> dict:
    repos_dir = WORKSPACE_ROOT.parent
    owner = _origin_owner(WORKSPACE_ROOT)
    return {
        "repos_dir": str(repos_dir),
        "ai_personal_dir": str(repos_dir / "ai-personal"),
        "notes_dir": str(repos_dir / "ai-projects"),
        "sources": [{"type": "github", "owner": owner}] if owner else [],
        "wezterm_shell": "login",
    }


def load_config() -> dict:
    """Defaults overlaid with the file; never writes."""
    cfg = default_config()
    path = config_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise SystemExit(f"Cannot read {path}: {exc}")
        if isinstance(data, dict):
            cfg.update(data)
    return cfg


def save_config(cfg: dict) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return path


def ensure_config() -> dict:
    """Create the config file with defaults on first run."""
    cfg = load_config()
    if not config_path().is_file():
        save_config(cfg)
    return cfg


def get_value(cfg: dict, key: str):
    if key not in cfg:
        raise KeyError(key)
    return cfg[key]
