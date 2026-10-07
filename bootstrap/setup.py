"""`setup`: config -> clone (optional) -> link -> application installers (optional)."""
from __future__ import annotations

import argparse
import subprocess
import sys

from . import clone, config, link

IS_WINDOWS = sys.platform.startswith("win")


def _confirm(question: str, non_interactive: bool) -> bool:
    if non_interactive or not sys.stdin.isatty():
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def _installers() -> list[str]:
    apps = config.WORKSPACE_ROOT / "applications"
    names = (["powershell/install_powershell.py", "wezterm/install_wezterm.py",
              "wsl/install_wsl.py", "yazi/install_yazi.py"]
             if IS_WINDOWS else ["yazi/install_yazi.py", "helix/install_helix.py"])
    return [str(apps / n) for n in names]


def run(args) -> int:
    if args.non_interactive and args.install_applications:
        print("--non-interactive cannot be combined with --install-applications: the "
              "application installers have their own prompts.", file=sys.stderr)
        return 2
    config.ensure_config()
    print(f"Config: {config.config_path()}")

    if not args.skip_clone:
        if _confirm("Pick and clone repositories now?", args.non_interactive):
            ns = argparse.Namespace(repos_dir=None, source=None, select=None, all=False)
            if clone.run(ns) != 0:
                print("Clone step reported problems; continuing.", file=sys.stderr)
        else:
            print("Skipping clone (run `clone` any time).")

    rc = 0
    if not args.skip_link:
        rc = link.run()

    if args.install_applications or _confirm("Run the application installers now?", args.non_interactive):
        for script in _installers():
            print(f"Running {script}...")
            if subprocess.run([sys.executable, script]).returncode != 0:
                print(f"Installer failed: {script}", file=sys.stderr)
                rc = 1
    else:
        print("Application installers not run (use --install-applications).")
    print("Run `doctor` to verify.")
    return rc
