"""
Shared Homebrew helpers for the Linux application installers (lazygit, helix).

Homebrew is used on Linux because these tools are missing or outdated in the
apt repos of LTS releases, and `brew upgrade` keeps them current. The helpers
install Homebrew when it is missing (apt prerequisites, then the official
installer), add its `shellenv` line to ~/.bashrc and ~/.zshrc, and install
only the formulae that are not installed yet. They also deploy config files,
asking before replacing one that differs. Safe to re-run.
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

IS_LINUX = platform.system() == "Linux"

INSTALL_SCRIPT_URL = "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
APT_PREREQUISITES = ["build-essential", "procps", "curl", "file", "git"]
SHELL_RC_FILES = (".bashrc", ".zshrc")


def step(msg):
    print(f"\n==> {msg}")


def run(cmd, **kwargs):
    print(f"$ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, **kwargs)


def require_linux(app):
    """Exit with a message on anything but Linux (only Linux is supported for now)."""
    if not IS_LINUX:
        print(f"The {app} installer supports Linux only for now.", file=sys.stderr)
        sys.exit(1)


def brew_candidates():
    return [Path("/home/linuxbrew/.linuxbrew/bin/brew"), Path.home() / ".linuxbrew" / "bin" / "brew"]


def find_brew():
    """Return the brew executable, also when it is not on PATH yet."""
    found = shutil.which("brew")
    if found:
        return Path(found)
    return next((p for p in brew_candidates() if p.exists()), None)


def shellenv_line(brew):
    return f'eval "$({brew} shellenv)"'


def add_shellenv(rc_text, line):
    """Return rc_text with the shellenv line appended, or None if brew is already set up."""
    for existing in rc_text.splitlines():
        stripped = existing.strip()
        if not stripped.startswith("#") and "brew shellenv" in stripped:
            return None
    separator = "" if not rc_text or rc_text.endswith("\n\n") else ("\n" if rc_text.endswith("\n") else "\n\n")
    return f"{rc_text}{separator}{line}\n"


def missing(installed, wanted):
    return [name for name in wanted if name not in installed]


def config_home():
    return Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"))


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


def deploy_config_file(source, target):
    """Copy source to target; ask before replacing a differing file (backup kept)."""
    source_text = source.read_text(encoding="utf-8")
    action = config_action(target, source_text)
    if action == "unchanged":
        print(f"{target} is up to date.")
        return
    if action == "differs":
        answer = ask(f"{target} differs from {source}. Replace it (backup kept)? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Kept the existing config.")
            return
        shutil.copy2(target, target.with_name(target.name + ".bak"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source_text, encoding="utf-8")
    print(f"Wrote {target}")


def install_brew():
    step("Installing Homebrew")
    if shutil.which("apt-get"):
        missing_tools = [t for t in ("curl", "git", "make", "gcc") if not shutil.which(t)]
        if missing_tools:
            run(["sudo", "apt-get", "update"], check=True)
        run(["sudo", "apt-get", "install", "-y", *APT_PREREQUISITES], check=True)
    else:
        print("No apt-get found: make sure build tools, curl, file and git are installed.")
    script = subprocess.run(["curl", "-fsSL", INSTALL_SCRIPT_URL], check=True,
                            capture_output=True, text=True).stdout
    run(["/bin/bash", "-c", script], check=True, env={**os.environ, "NONINTERACTIVE": "1"})
    brew = find_brew()
    if brew is None:
        raise SystemExit("Homebrew was installed but brew could not be found.")
    return brew


def add_to_shell_rc(brew):
    line = shellenv_line(brew)
    for name in SHELL_RC_FILES:
        rc = Path.home() / name
        if name != ".bashrc" and not rc.exists():
            continue
        text = rc.read_text(encoding="utf-8") if rc.exists() else ""
        updated = add_shellenv(text, line)
        if updated is None:
            print(f"{rc}: brew shellenv already set up.")
        else:
            rc.write_text(updated, encoding="utf-8")
            print(f"{rc}: added brew shellenv (open a new terminal to use brew).")


def ensure_brew():
    step("Checking Homebrew")
    brew = find_brew()
    if brew is None:
        brew = install_brew()
    else:
        print(f"Found {brew}")
    add_to_shell_rc(brew)
    return brew


def install_formulae(brew, formulae):
    step(f"Installing {', '.join(formulae)}")
    listed = subprocess.run([str(brew), "list", "--formula", "-1"], check=True,
                            capture_output=True, text=True).stdout
    todo = missing(set(listed.split()), formulae)
    if not todo:
        print("All already installed (update with `brew upgrade`).")
        return
    run([str(brew), "install", *todo], check=True)
