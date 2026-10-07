#!/usr/bin/env python3
"""
Installs the Helix editor and language servers with Homebrew (Linux only for now):
  - installs Homebrew if it is missing (see applications/brew_common.py)
  - installs helix (the command is `hx`) and one language server per language
    used in my repos, so Helix gets completion, diagnostics and go-to-definition
    (`hx --health <language>` shows what Helix finds)

Safe to re-run: only missing formulae are installed. Update with `brew upgrade`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import brew_common  # noqa: E402

# Helix picks these up from PATH without configuration.
LANGUAGE_SERVERS = {
    "TypeScript / JavaScript": ["typescript-language-server"],
    "Python": ["ty", "ruff"],
    "Java": ["jdtls"],
    "Shell": ["bash-language-server"],
    "Markdown": ["marksman"],
    "YAML": ["yaml-language-server"],
    "JSON / HTML / CSS": ["vscode-langservers-extracted"],
}


def formulae():
    names = ["helix"]
    for servers in LANGUAGE_SERVERS.values():
        names += [s for s in servers if s not in names]
    return names


def main():
    brew_common.require_linux("Helix")
    brew = brew_common.ensure_brew()
    brew_common.install_formulae(brew, formulae())
    print("\nDone. Run `hx --health` to see the languages Helix supports, `hx --tutor` to learn it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
