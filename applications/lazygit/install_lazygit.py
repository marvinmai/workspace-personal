#!/usr/bin/env python3
"""
Installs lazygit and configures it (Linux only for now):
  - installs Homebrew if it is missing (see applications/brew_common.py)
  - installs lazygit and delta with Homebrew (lazygit is not in the apt repos of
    Ubuntu 24.04 based releases; `brew upgrade` keeps both current)
  - deploys config.yml from this folder to ~/.config/lazygit/config.yml: Helix
    (`hx`) as the editor and delta as the diff renderer (`|` cycles renderers)
  - adds an `lg` shortcut for lazygit to ~/.bashrc and ~/.zshrc (managed block)

Safe to re-run: only missing formulae are installed, and an existing config that
differs from config.yml is replaced only after asking (the old one is kept as
config.yml.bak).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import brew_common  # noqa: E402

FORMULAE = ["lazygit", "git-delta"]
CONFIG_SOURCE = Path(__file__).resolve().parent / "config.yml"
SHORTCUT_BEGIN = "# >>> lazygit-setup: lg >>>"
SHORTCUT_END = "# <<< lazygit-setup: lg <<<"


def config_path():
    return brew_common.config_home() / "lazygit" / "config.yml"


def deploy_config():
    brew_common.step("Configuring lazygit")
    brew_common.deploy_config_file(CONFIG_SOURCE, config_path())


def shortcut_block():
    return "\n".join([
        SHORTCUT_BEGIN,
        "# Managed by workspace-personal (applications/lazygit/install_lazygit.py).",
        'lg() { lazygit "$@"; }',
        SHORTCUT_END,
    ]) + "\n"


def upsert_shortcut(text):
    """Return text with the managed `lg` block added or replaced in place."""
    block = shortcut_block()
    if SHORTCUT_BEGIN in text:
        start = text.index(SHORTCUT_BEGIN)
        end = text.find(SHORTCUT_END, start)
        if end < 0:
            raise ValueError("malformed lazygit-setup block (end marker missing)")
        rest = text[end + len(SHORTCUT_END):]
        return text[:start] + block + rest.lstrip("\n")
    sep = "" if not text or text.endswith("\n") else "\n"
    return text + sep + ("\n" if text else "") + block


def install_shortcut():
    brew_common.step("Adding the `lg` shortcut")
    for name in brew_common.SHELL_RC_FILES:
        rc = Path.home() / name
        if name != ".bashrc" and not rc.exists():
            continue
        old = rc.read_text(encoding="utf-8") if rc.exists() else ""
        new = upsert_shortcut(old)
        if new == old:
            print(f"{rc}: up to date")
        else:
            rc.write_text(new, encoding="utf-8", newline="\n")
            print(f"{rc}: added `lg` (open a new shell)")


def main():
    brew_common.require_linux("lazygit")
    brew = brew_common.ensure_brew()
    brew_common.install_formulae(brew, FORMULAE)
    deploy_config()
    install_shortcut()
    print("\nDone. Run `lg` (or `lazygit`) inside a repository. The editor is Helix: run applications/helix/install_helix.py too.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
