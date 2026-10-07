#!/usr/bin/env python3
"""
Installs lazygit and configures it (Linux only for now):
  - installs Homebrew if it is missing (see applications/brew_common.py)
  - installs lazygit and delta with Homebrew (lazygit is not in the apt repos of
    Ubuntu 24.04 based releases; `brew upgrade` keeps both current)
  - deploys config.yml from this folder to ~/.config/lazygit/config.yml: Helix
    (`hx`) as the editor and delta as the diff renderer (`|` cycles renderers)

Safe to re-run: only missing formulae are installed, and an existing config that
differs from config.yml is replaced only after asking (the old one is kept as
config.yml.bak).
"""

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import brew_common  # noqa: E402

FORMULAE = ["lazygit", "git-delta"]
CONFIG_SOURCE = Path(__file__).resolve().parent / "config.yml"


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "lazygit" / "config.yml"


def config_action(target, source_text):
    """Return "write" (missing or empty), "unchanged", or "differs"."""
    if not target.exists():
        return "write"
    current = target.read_text(encoding="utf-8")
    if not current.strip():
        return "write"
    return "unchanged" if current == source_text else "differs"


def ask(prompt):
    if not sys.stdin.isatty():
        return ""
    try:
        return input(prompt)
    except EOFError:
        return ""


def deploy_config():
    brew_common.step("Configuring lazygit")
    target = config_path()
    source_text = CONFIG_SOURCE.read_text(encoding="utf-8")
    action = config_action(target, source_text)
    if action == "unchanged":
        print(f"{target} is up to date.")
        return
    if action == "differs":
        answer = ask(f"{target} differs from {CONFIG_SOURCE}. Replace it (backup kept)? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Kept the existing config.")
            return
        shutil.copy2(target, target.with_name(target.name + ".bak"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source_text, encoding="utf-8")
    print(f"Wrote {target}")


def main():
    brew_common.require_linux("lazygit")
    brew = brew_common.ensure_brew()
    brew_common.install_formulae(brew, FORMULAE)
    deploy_config()
    print("\nDone. Run `lazygit` inside a repository. The editor is Helix: run applications/helix/install_helix.py too.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
