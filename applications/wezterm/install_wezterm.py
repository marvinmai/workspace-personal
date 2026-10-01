#!/usr/bin/env python3
"""
Installs WezTerm and configures which shell it launches by default, on Windows,
Linux, and macOS:
  - installs wezterm via the platform's package manager (winget / brew / pacman / apt / dnf)
  - on Windows, lets you pick the default shell approach - WSL2, PowerShell 7
    (pwsh.exe), or cmd.exe - explaining the trade-offs of each; on Linux/macOS,
    your login shell (default, WezTerm's own behavior) or PowerShell 7 (pwsh)
  - deploys the shared look/behavior from wezterm.base.lua as a managed "base" block
  - wires the selected choice into the WezTerm config (Linux/macOS:
    ~/.config/wezterm/wezterm.lua; Windows: ~/.wezterm.lua)
  - on the "pwsh" approach: installs PowerShell 7 if it isn't already on PATH

The "wsl" approach only points WezTerm at an existing WSL instance (default
"personal");
creating that instance - installing the distro, renaming it, setting the hostname,
copying in a working directory, JetBrains Gateway - is handled separately by
`../wsl/install_wsl.py`. If the instance isn't there yet, this script still writes
the config and reminds you to run that one.

Independently of the *default* shell, on Windows the script also wires a second
managed block ("shell-switching") that lets you jump between shells live: it
builds a WezTerm launch menu listing every installed WSL distro plus PowerShell
and cmd, and binds Ctrl+Shift+E to a fuzzy launcher that opens any of them in
a new tab. So you can keep, say, WSL as the default and still pop a PowerShell tab
on demand without editing the config. Disable it with --no-shell-switching.

Safe to re-run: re-running lets you switch the shell approach at any time (pass
--shell-approach non-interactively, or answer the prompt again) - the blocks this
script manages inside .wezterm.lua get replaced in place. Anything you added to
.wezterm.lua by hand, outside those managed blocks, is left untouched.
Only configs using the standard `local config = wezterm.config_builder()` /
`return config` builder shape are modified. Direct-table and other unrecognized
configs are refused without being modified because their shape cannot be merged
safely with the managed `config` blocks.
"""

import argparse
import codecs
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

MARKER_BEGIN = "-- >>> wezterm-setup: {name} >>>"
MARKER_END = "-- <<< wezterm-setup: {name} <<<"
MANAGED_BEGIN_RE = re.compile(
    re.escape("-- >>> wezterm-setup: ") + r"(?P<name>.*?)" + re.escape(" >>>")
)
MANAGED_END_RE = re.compile(
    re.escape("-- <<< wezterm-setup: ") + r"(?P<name>.*?)" + re.escape(" <<<")
)

DEFAULT_WSL_INSTANCE_NAME = os.environ.get("WORKSPACE_PERSONAL_WSL_INSTANCE", "personal")

SHELL_APPROACH_HELP = """
Which shell should WezTerm launch by default?

  1) wsl   Launch into WSL2 (bash/zsh on a real Linux distro, e.g. "personal").
           + Matches Linux/Mac tooling assumptions (apt, bash idioms, Docker,
             install scripts) - least friction with docs/tutorials/CI
           + Closest behavior to your actual Linux/Mac machines
           - Slower I/O when crossing into /mnt/c/... - keep working files
             inside the Linux filesystem (e.g. ~/projects)
           - One more moving part (a whole Linux distro) to keep updated
           (set the instance up with ../wsl/install_wsl.py)

  2) pwsh  Launch into PowerShell 7 (pwsh.exe).
           + One shell binary/profile shareable across Windows/Linux/Mac
           + No WSL overhead, tight native Windows integration
           - Most real-world docs/tutorials/CI assume bash, so translating
             commands is a recurring source of friction

  3) cmd   Stay on cmd.exe (WezTerm's native Windows default).
           + Zero setup, always available
           - No cross-platform consistency; some legacy quirks differ from
             bash/PowerShell (e.g. `cd` won't switch drives - needs `cd /d`)
"""

NON_WINDOWS_SHELL_APPROACH_HELP = """
Which shell should WezTerm launch by default?

  login  Your account's login shell ($SHELL: bash, zsh, ...). This is WezTerm's
         own default, so nothing is forced. Recommended on Linux/macOS.
         + No extra dependency, every helper in this workspace already works
  pwsh   Launch into PowerShell 7 (`pwsh`).
         + One shell binary/profile shareable across Linux and macOS
         - Most real-world docs/tutorials/CI assume bash, so translating
           commands is a recurring source of friction

The `wsl` and `cmd` approaches are Windows-only and are not available here.
"""

BASE_FILE = Path(__file__).resolve().with_name("wezterm.base.lua")


def step(msg):
    print(f"\n==> {msg}")


def ask(prompt):
    try:
        return input(prompt)
    except EOFError:
        return ""


def run(cmd, **kwargs):
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, **kwargs)


def read_config_text(path):
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


def write_config_text(path, content):
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(content)


def config_newline(content):
    if "\r\n" in content:
        return "\r\n"
    if "\r" in content:
        return "\r"
    if "\n" in content:
        return "\n"
    return os.linesep


# --- 1. Install WezTerm ------------------------------------------------------

def find_wezterm_executable():
    for name in ("wezterm", "wezterm.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def install_package(display_name, winget_id=None, brew_pkg=None, brew_cask=None,
                     pacman_pkg=None, apt_pkg=None, dnf_pkg=None, manual_hint=None):
    step(f"Installing {display_name}")

    if IS_WINDOWS and winget_id and shutil.which("winget"):
        result = run(["winget", "install", "--id", winget_id, "-e",
                      "--accept-package-agreements", "--accept-source-agreements"])
        if result.returncode == 0:
            return True
        print(f"{display_name} installation failed with exit code {result.returncode}.")
        if manual_hint:
            print(manual_hint)
        return False

    if IS_MAC and brew_cask and shutil.which("brew"):
        result = run(["brew", "install", "--cask", brew_cask])
        if result.returncode == 0:
            return True
        print(f"{display_name} installation failed with exit code {result.returncode}.")
        if manual_hint:
            print(manual_hint)
        return False

    if brew_pkg and shutil.which("brew"):
        result = run(["brew", "install", brew_pkg])
        if result.returncode == 0:
            return True
        print(f"{display_name} installation failed with exit code {result.returncode}.")
        if manual_hint:
            print(manual_hint)
        return False

    if IS_LINUX and pacman_pkg and shutil.which("pacman"):
        result = run(["sudo", "pacman", "-S", "--needed", "--noconfirm", pacman_pkg])
        if result.returncode == 0:
            return True
        print(f"{display_name} installation failed with exit code {result.returncode}.")
        if manual_hint:
            print(manual_hint)
        return False

    if IS_LINUX and apt_pkg and shutil.which("apt-get"):
        result = run(["sudo", "apt-get", "install", "-y", apt_pkg])
        if result.returncode == 0:
            return True
        print(f"{display_name} installation failed with exit code {result.returncode}.")
        if manual_hint:
            print(manual_hint)
        return False

    if IS_LINUX and dnf_pkg and shutil.which("dnf"):
        result = run(["sudo", "dnf", "install", "-y", dnf_pkg])
        if result.returncode == 0:
            return True
        print(f"{display_name} installation failed with exit code {result.returncode}.")
        if manual_hint:
            print(manual_hint)
        return False

    print(f"Could not find a supported package manager for {display_name}.")
    if manual_hint:
        print(manual_hint)
    print("The requested installation was not completed.")
    return False


def install_wezterm():
    existing = find_wezterm_executable()
    if existing:
        print(f"WezTerm is already installed at: {existing}")
        return True

    return install_package(
        "WezTerm",
        winget_id="wez.wezterm",
        brew_cask="wezterm",
        pacman_pkg="wezterm",
        dnf_pkg="wezterm",
        manual_hint=(
            "Debian/Ubuntu needs the WezTerm apt repo first - see "
            "https://wezterm.org/install/linux.html#debian-and-ubuntu"
        ),
    )


def find_pwsh_executable():
    for name in ("pwsh", "pwsh.exe"):
        found = shutil.which(name)
        if found:
            return found

    candidates = []
    if IS_WINDOWS:
        roots = []
        for env_var in ("ProgramW6432", "ProgramFiles", "LOCALAPPDATA"):
            root = os.environ.get(env_var)
            if root:
                roots.append(Path(root))
        for root in roots:
            candidates.extend(
                (
                    root / "PowerShell" / "7" / "pwsh.exe",
                    root / "Programs" / "PowerShell" / "7" / "pwsh.exe",
                    root / "Microsoft" / "WindowsApps" / "pwsh.exe",
                )
            )
            for base in (
                root / "PowerShell",
                root / "Programs" / "PowerShell",
            ):
                if base.is_dir():
                    candidates.extend(base.glob("*/pwsh.exe"))
    elif IS_MAC:
        candidates.extend(
            (
                Path("/opt/homebrew/bin/pwsh"),
                Path("/usr/local/bin/pwsh"),
            )
        )

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def add_executable_to_path(executable):
    parent = str(Path(executable).parent)
    entries = os.environ.get("PATH", "").split(os.pathsep)
    if not any(os.path.normcase(entry) == os.path.normcase(parent) for entry in entries):
        os.environ["PATH"] = parent + os.pathsep + os.environ.get("PATH", "")


def install_pwsh():
    step("Checking PowerShell 7 (pwsh)")
    existing = find_pwsh_executable()
    if existing:
        add_executable_to_path(existing)
        print(f"pwsh is already installed at: {existing}")
        return True

    if not install_package(
            "PowerShell 7 (pwsh)",
            winget_id="Microsoft.PowerShell",
            brew_cask="powershell",
            manual_hint=(
                "See https://learn.microsoft.com/powershell/scripting/install/installing-powershell "
                "- apt/dnf/pacman all need Microsoft's package repo added first."
            ),
    ):
        return False

    installed = find_pwsh_executable()
    if not installed:
        print("PowerShell 7 installation reported success, but the pwsh executable "
              "could not be located. Shell switching was not configured.")
        return False
    add_executable_to_path(installed)
    print(f"Found newly installed pwsh at: {installed}")
    return True


# --- 2. Ask which shell approach to use ---------------------------------------

def supported_shell_approaches():
    return ("wsl", "pwsh", "cmd") if IS_WINDOWS else ("login", "pwsh")


def prompt_shell_approach():
    step("Choose the default shell approach")
    choices = supported_shell_approaches()
    print(SHELL_APPROACH_HELP if IS_WINDOWS else NON_WINDOWS_SHELL_APPROACH_HELP)
    choices_text = "/".join(choices)
    while True:
        choice = ask(f"Choose [{choices_text}]: ").strip().lower()
        if not choice and not IS_WINDOWS:
            return "login"
        if choice in choices:
            return choice
        print(f"Please type one of: {', '.join(choices)}.")


def list_wsl_distros():
    """Return the installed distro NAMEs (as `wsl` identifies them), or [] if
    WSL itself isn't installed yet."""
    try:
        result = subprocess.run(
            ["wsl", "-l", "-q"], capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    if result.returncode != 0:
        return []

    text = decode_wsl_output(result.stdout)
    return [line.strip() for line in text.splitlines() if line.strip()]


def decode_wsl_output(raw):
    """Decode the UTF-16 or UTF-8 output emitted by different WSL builds."""
    if isinstance(raw, str):
        return raw
    if raw.startswith(codecs.BOM_UTF32_LE) or raw.startswith(codecs.BOM_UTF32_BE):
        return raw.decode("utf-32")
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return raw.decode("utf-16")
    if b"\x00" in raw:
        return raw.decode("utf-16-le")
    return raw.decode("utf-8", errors="replace")


def is_distro_installed(instance_name):
    return any(d.lower() == instance_name.lower() for d in list_wsl_distros())


# --- 3. Wire the chosen approach into .wezterm.lua ----------------------------

def wezterm_config_path(override=None):
    override = override or os.environ.get("WEZTERM_CONFIG_FILE")
    if override:
        return Path(override)
    legacy = Path.home() / ".wezterm.lua"
    if IS_WINDOWS:
        return legacy
    # Linux/macOS: WezTerm prefers $XDG_CONFIG_HOME/wezterm/wezterm.lua, then
    # ~/.config/wezterm/wezterm.lua, then ~/.wezterm.lua. Reuse whichever exists
    # (first match wins, as in WezTerm); create the XDG location otherwise.
    xdg = os.environ.get("XDG_CONFIG_HOME")
    candidates = ([Path(xdg) / "wezterm" / "wezterm.lua"] if xdg else []) + [
        Path.home() / ".config" / "wezterm" / "wezterm.lua",
        legacy,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def lua_string_literal(value):
    """Return a single-quoted Lua string literal for a dynamic value."""
    escaped = []
    for char in str(value):
        codepoint = ord(char)
        if char == "\\":
            escaped.append(r"\\")
        elif char == "'":
            escaped.append(r"\'")
        elif char == "\n":
            escaped.append(r"\n")
        elif char == "\r":
            escaped.append(r"\r")
        elif char == "\t":
            escaped.append(r"\t")
        elif char == "\b":
            escaped.append(r"\b")
        elif char == "\f":
            escaped.append(r"\f")
        elif codepoint < 0x20 or codepoint == 0x7F:
            escaped.append(f"\\{codepoint:03d}")
        else:
            escaped.append(char)
    return "'" + "".join(escaped) + "'"


def shell_approach_lines(approach, wsl_instance_name):
    if approach == "wsl":
        if not IS_WINDOWS:
            raise ValueError("the 'wsl' shell approach is only available on Windows")
        return [
            f"config.default_domain = {lua_string_literal(f'WSL:{wsl_instance_name}')}"
        ]
    if approach == "login":
        if IS_WINDOWS:
            raise ValueError("the 'login' shell approach is only available on Linux/macOS")
        return ["-- login shell: WezTerm launches $SHELL (no default_prog override)"]
    if approach == "pwsh":
        executable = "pwsh.exe" if IS_WINDOWS else "pwsh"
        return [
            f"config.default_prog = {{ {lua_string_literal(executable)}, '-NoLogo' }}"
        ]
    if approach == "cmd":
        if not IS_WINDOWS:
            raise ValueError("the 'cmd' shell approach is only available on Windows")
        return [f"config.default_prog = {{ {lua_string_literal('cmd.exe')} }}"]
    raise ValueError(f"unknown shell approach: {approach}")


def shell_switching_lines(wsl_distros, pwsh_available):
    """Lines for the 'shell-switching' block: a launch menu of every reachable
    shell plus a launcher keybinding, so you can hop between shells regardless of
    which one is the default.

    launch_menu entries are WezTerm SpawnCommand objects; WSL entries use the
    `domain` field so they spawn into the auto-registered `WSL:<name>` multiplexer
    domain (correct pane titles / cwd tracking) rather than a raw wsl.exe process.
    config.keys merges with WezTerm's default bindings, so this only *adds*
    Ctrl+Shift+E and leaves the built-in shortcuts intact.
    """
    menu = []
    for distro in wsl_distros:
        menu.append(
            f"  {{ label = {lua_string_literal(f'WSL: {distro}')}, "
            f"domain = {{ DomainName = {lua_string_literal(f'WSL:{distro}')} }} }},"
        )
    # The Windows entries must pin the built-in 'local' domain: without it they
    # inherit config.default_domain (e.g. 'WSL:personal') and WezTerm tries to run
    # pwsh.exe/cmd.exe *inside* WSL, where they don't exist - the process exits
    # immediately and the new tab closes before you can see anything.
    #
    # PowerShell 7 (pwsh) when it's installed, otherwise the always-present
    # Windows PowerShell - either way we advertise one PowerShell entry.
    powershell = "pwsh.exe" if pwsh_available else "powershell.exe"
    menu.append(
        f"  {{ label = 'Windows: PowerShell', domain = {{ DomainName = 'local' }}, "
        f"args = {{ {lua_string_literal(powershell)}, '-NoLogo' }} }},"
    )
    menu.append("  { label = 'Windows: cmd', domain = { DomainName = 'local' }, "
                "args = { 'cmd.exe' } },")

    return (
        ["config.launch_menu = {"]
        + menu
        + ["}",
           "config.keys = config.keys or {}",
           "table.insert(config.keys, { key = 'E', mods = 'CTRL|SHIFT',",
           "  action = wezterm.action.ShowLauncherArgs "
           "{ flags = 'FUZZY|LAUNCH_MENU_ITEMS' } })"]
    )


CONFLICT_PATTERN = r"^\s*config\.(default_domain|default_prog)\s*="
SWITCHING_CONFLICT_PATTERN = r"^\s*config\.launch_menu\s*="


class UnsupportedConfigFormatError(ValueError):
    """Raised when a WezTerm config cannot safely accept managed blocks."""


class ManagedMarkerError(ValueError):
    """Raised when a managed WezTerm marker namespace is malformed."""


def validate_managed_markers(content, path):
    """Refuse duplicate, nested, unmatched, or out-of-order managed blocks."""
    events = [
        (match.start(), "begin", match.group("name"))
        for match in MANAGED_BEGIN_RE.finditer(content)
    ] + [
        (match.start(), "end", match.group("name"))
        for match in MANAGED_END_RE.finditer(content)
    ]
    active_name = None
    completed = set()
    for _position, kind, name in sorted(events):
        if not name.strip():
            raise ManagedMarkerError(
                f"Malformed WezTerm markers in {path}: marker name is empty. "
                "No changes were written."
            )
        if kind == "begin":
            if active_name is not None:
                raise ManagedMarkerError(
                    f"Malformed WezTerm markers in {path}: nested managed blocks "
                    "are not supported. No changes were written."
                )
            if name in completed:
                raise ManagedMarkerError(
                    f"Malformed WezTerm markers in {path}: managed block '{name}' "
                    "appears more than once. No changes were written."
                )
            active_name = name
        elif active_name is None:
            raise ManagedMarkerError(
                f"Malformed WezTerm markers in {path}: end marker '{name}' has no "
                "matching begin marker. No changes were written."
            )
        elif active_name != name:
            raise ManagedMarkerError(
                f"Malformed WezTerm markers in {path}: end marker '{name}' does "
                f"not match active block '{active_name}'. No changes were written."
            )
        else:
            completed.add(active_name)
            active_name = None

    if active_name is not None:
        raise ManagedMarkerError(
            f"Malformed WezTerm markers in {path}: begin marker '{active_name}' "
            "has no matching end marker. No changes were written."
        )


def _lua_long_bracket_start(content, start):
    """Return ``(body_start, closing_delimiter)`` for a Lua long bracket."""
    if start >= len(content) or content[start] != "[":
        return None
    cursor = start + 1
    while cursor < len(content) and content[cursor] == "=":
        cursor += 1
    if cursor >= len(content) or content[cursor] != "[":
        return None
    equals = content[start + 1:cursor]
    return cursor + 1, "]" + equals + "]"


def _lua_tokens_with_positions(content):
    """Tokenize enough Lua syntax to validate config shape and return position."""
    tokens = []
    cursor = 0
    while cursor < len(content):
        start = cursor
        char = content[cursor]
        if char.isspace() or char == "\ufeff":
            cursor += 1
            continue

        if content.startswith("--", cursor):
            long_bracket = _lua_long_bracket_start(content, cursor + 2)
            if long_bracket:
                body_start, closing = long_bracket
                end = content.find(closing, body_start)
                if end == -1:
                    tokens.append(("invalid", None, start, len(content)))
                    cursor = len(content)
                else:
                    cursor = end + len(closing)
            else:
                line_ends = [
                    end for end in (
                        content.find("\r", cursor + 2),
                        content.find("\n", cursor + 2),
                    ) if end != -1
                ]
                end = min(line_ends) if line_ends else -1
                cursor = len(content) if end == -1 else end + 1
            continue

        if char in ("'", '"'):
            quote = char
            cursor += 1
            closed = False
            while cursor < len(content):
                if content[cursor] == "\\":
                    cursor += 2
                elif content[cursor] == quote:
                    cursor += 1
                    closed = True
                    break
                else:
                    cursor += 1
            tokens.append(("literal" if closed else "invalid", None, start, cursor))
            continue

        if char == "[":
            long_bracket = _lua_long_bracket_start(content, cursor)
            if long_bracket:
                body_start, closing = long_bracket
                end = content.find(closing, body_start)
                if end == -1:
                    tokens.append(("invalid", None, start, len(content)))
                    cursor = len(content)
                else:
                    cursor = end + len(closing)
                    tokens.append(("literal", None, start, cursor))
                continue

        if char.isalpha() or char == "_":
            end = cursor + 1
            while end < len(content) and (content[end].isalnum() or content[end] == "_"):
                end += 1
            tokens.append(("identifier", content[cursor:end], start, end))
            cursor = end
            continue

        if char.isdigit():
            end = cursor + 1
            while end < len(content) and (
                content[end].isalnum() or content[end] in "._+-"
            ):
                end += 1
            tokens.append(("literal", None, start, end))
            cursor = end
            continue

        tokens.append(("symbol", char, start, start + 1))
        cursor += 1

    return tokens


def _lua_tokens(content):
    return [(kind, value) for kind, value, _start, _end in
            _lua_tokens_with_positions(content)]


def _return_starts_table(tokens, index):
    next_index = index + 1
    while next_index < len(tokens) and tokens[next_index][0:2] == ("symbol", "("):
        next_index += 1
    return next_index < len(tokens) and tokens[next_index][0:2] == ("symbol", "{")


def _compatible_return(tokens, index):
    expression = [token[0:2] for token in tokens[index + 1:]]
    if expression and expression[-1] == ("symbol", ";"):
        expression = expression[:-1]
    return expression in (
        [("identifier", "config")],
        [("symbol", "("), ("identifier", "config"), ("symbol", ")")],
    )


def _is_config_builder_declaration(tokens, index):
    expected = [
        ("identifier", "local"),
        ("identifier", "config"),
        ("symbol", "="),
        ("identifier", "wezterm"),
        ("symbol", "."),
        ("identifier", "config_builder"),
        ("symbol", "("),
        ("symbol", ")"),
    ]
    return [token[0:2] for token in tokens[index:index + len(expected)]] == expected


def _analyze_lua_config(content):
    tokens = _lua_tokens_with_positions(content)
    blocks = []
    non_function_returns = []
    top_level_returns = []
    returned_tables = []
    config_declarations = []
    malformed = False

    for index, (kind, value, _start, _end) in enumerate(tokens):
        if kind == "invalid":
            malformed = True
            continue
        if kind == "identifier":
            if value == "local" and not blocks and _is_config_builder_declaration(tokens, index):
                config_declarations.append(index)
            elif value == "return":
                if "function" not in blocks:
                    non_function_returns.append(index)
                    if _return_starts_table(tokens, index):
                        returned_tables.append(index)
                    if not blocks:
                        top_level_returns.append(index)
            elif value == "function":
                blocks.append("function")
            elif value == "if":
                blocks.append("if")
            elif value == "for":
                blocks.append("for-pending")
            elif value == "while":
                blocks.append("while-pending")
            elif value == "repeat":
                blocks.append("repeat")
            elif value == "do":
                if blocks and blocks[-1] in ("for-pending", "while-pending"):
                    blocks[-1] = blocks[-1][:-len("-pending")]
                else:
                    blocks.append("do")
            elif value == "end":
                if not blocks or blocks[-1] == "repeat":
                    malformed = True
                else:
                    blocks.pop()
            elif value == "until":
                if not blocks or blocks[-1] != "repeat":
                    malformed = True
                else:
                    blocks.pop()

    if blocks:
        malformed = True
    return (
        tokens,
        non_function_returns,
        top_level_returns,
        returned_tables,
        config_declarations,
        malformed,
    )


def has_returned_table_config(content):
    """Return whether the config uses a parenthesized/commented top-level table."""
    _tokens, _non_function_returns, _returns, returned_tables, _declarations, _malformed = \
        _analyze_lua_config(content)
    return bool(returned_tables)


def _validate_builder_shape(content, path):
    (
        tokens,
        non_function_returns,
        top_level_returns,
        returned_tables,
        config_declarations,
        malformed,
    ) = _analyze_lua_config(content)
    if malformed:
        raise UnsupportedConfigFormatError(
            f"Cannot update {path}: the Lua block structure is not recognized. "
            "No changes were written."
        )
    if returned_tables:
        raise UnsupportedConfigFormatError(
            f"Cannot update {path}: it returns a Lua table directly "
            "(`return { ... }`, including parenthesized forms), which this "
            "installer cannot safely merge. "
            "No changes were written."
        )
    if len(config_declarations) != 1:
        raise UnsupportedConfigFormatError(
            f"Cannot update {path}: expected exactly one "
            "`local config = wezterm.config_builder()` declaration. "
            "No changes were written."
        )
    if len(top_level_returns) != 1:
        raise UnsupportedConfigFormatError(
            f"Cannot update {path}: expected exactly one top-level `return config`. "
            "No changes were written."
        )
    if len(non_function_returns) != len(top_level_returns):
        raise UnsupportedConfigFormatError(
            f"Cannot update {path}: return statements inside top-level control flow "
            "are not a recognized config shape. No changes were written."
        )

    declaration = config_declarations[0]
    returned = top_level_returns[0]
    if declaration > returned or not _compatible_return(tokens, returned):
        raise UnsupportedConfigFormatError(
            f"Cannot update {path}: the config does not return the declared builder "
            "as its final top-level statement. No changes were written."
        )
    return tokens[returned][2]


def upsert_marked_block(path, name, lines, conflict_pattern=None, conflict_label=None):
    """Create or update a managed marker block in `path` with `lines` as content.

    If the marker is already present, its content is replaced in place - this is
    what makes re-running the script with a different shell approach work. If the
    marker is not present yet, `conflict_pattern` is checked against the rest of
    the file so a setting you added by hand is never silently overwritten.
    Only the standard config-builder shape is modified; all other shapes are
    refused before writing.
    """
    begin = MARKER_BEGIN.format(name=name)
    end = MARKER_END.format(name=name)
    newline = os.linesep
    new_block = f"{begin}{newline}" + newline.join(lines) + f"{newline}{end}"

    if not path.exists():
        skeleton = (
            f"local wezterm = require 'wezterm'{newline}"
            f"local config = wezterm.config_builder(){newline}"
            f"{newline}{new_block}{newline}"
            f"{newline}return config{newline}"
        )
        write_config_text(path, skeleton)
        print(f"Created {path} with the selected shell approach.")
        return

    content = read_config_text(path)
    newline = config_newline(content)
    new_block = f"{begin}{newline}" + newline.join(lines) + f"{newline}{end}"
    if not content.replace("\ufeff", "").strip():
        skeleton = (
            f"local wezterm = require 'wezterm'{newline}"
            f"local config = wezterm.config_builder(){newline}"
            f"{newline}{new_block}{newline}"
            f"{newline}return config{newline}"
        )
        write_config_text(path, skeleton)
        print(f"Initialized {path} with the selected shell approach.")
        return

    validate_managed_markers(content, path)
    return_position = _validate_builder_shape(content, path)
    marker_re = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)

    if marker_re.search(content):
        # Use a callable replacement so backslashes/backrefs in new_block are
        # never interpreted by re.sub's string-replacement escaping rules.
        content = marker_re.sub(lambda _m: new_block, content, count=1)
        write_config_text(path, content)
        print(f"Updated the managed shell-approach block in {path}.")
        return

    if conflict_pattern and re.search(conflict_pattern, content, re.MULTILINE):
        label = conflict_label or name
        print(f"{label} is already set manually in {path} - leaving it untouched. "
              "Remove your manual setting by hand if you want this script to manage it.")
        return

    line_start = max(content.rfind("\n", 0, return_position),
                     content.rfind("\r", 0, return_position)) + 1
    prefix = content[:line_start]
    suffix = content[line_start:]
    separator = "" if not prefix or prefix.endswith(("\n", "\r")) else newline
    content = prefix + separator + new_block + newline * 2 + suffix

    write_config_text(path, content)
    print(f"Inserted the shell-approach block into {path}.")


def apply_shell_approach(approach, wsl_instance_name, config_file=None):
    step(f"Wiring WezTerm to use the '{approach}' shell approach")

    if approach not in supported_shell_approaches():
        raise ValueError(
            f"shell approach '{approach}' is not available on this platform"
        )

    config_path = wezterm_config_path(config_file)
    lines = shell_approach_lines(approach, wsl_instance_name)
    upsert_marked_block(
        config_path, "shell-approach", lines,
        conflict_pattern=CONFLICT_PATTERN,
        conflict_label="config.default_domain/default_prog",
    )


def base_lines():
    """The shared look/behavior lines from wezterm.base.lua (a fragment, not a full config)."""
    text = BASE_FILE.read_text(encoding="utf-8").replace("\r\n", "\n")
    return text.rstrip("\n").split("\n")


def apply_base(config_file=None):
    step("Deploying the shared WezTerm base (look and behavior)")
    config_path = wezterm_config_path(config_file)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    upsert_marked_block(config_path, "base", base_lines())
    print(f"Config file: {config_path}")


def apply_shell_switching(wsl_distros, pwsh_available, config_file=None):
    step("Wiring the launch menu + launcher key for switching shells")

    if not IS_WINDOWS:
        print("Shell switching wiring only applies on Windows - skipping.")
        return

    config_path = wezterm_config_path(config_file)
    lines = shell_switching_lines(wsl_distros, pwsh_available)
    upsert_marked_block(
        config_path, "shell-switching", lines,
        conflict_pattern=SWITCHING_CONFLICT_PATTERN,
        conflict_label="config.launch_menu",
    )
    print("Press Ctrl+Shift+E in WezTerm to pick a shell (WSL / PowerShell / cmd).")


# --- main -----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--shell-approach", choices=supported_shell_approaches(), default=None,
                         help="Default shell WezTerm should launch. If omitted (and "
                              "--skip-config isn't given), you'll be prompted interactively "
                              "with the trade-offs of each option.")
    parser.add_argument("--wsl-instance-name", default=DEFAULT_WSL_INSTANCE_NAME,
                         help=f"WSL instance WezTerm should target for the 'wsl' approach "
                              f"(default: {DEFAULT_WSL_INSTANCE_NAME}). Set it up with "
                              f"../wsl/install_wsl.py.")
    parser.add_argument("--config-file", default=None,
                              help="Path to the WezTerm Lua config to update (overrides "
                                   "WEZTERM_CONFIG_FILE and the platform default).")
    parser.add_argument("--skip-config", action="store_true",
                         help="Don't touch the WezTerm config at all - just install packages.")
    parser.add_argument("--no-base", dest="base", action="store_false", default=True,
                         help="Skip the shared base block from wezterm.base.lua "
                              "(look and behavior).")
    parser.add_argument("--no-shell-switching", dest="shell_switching",
                         action="store_false", default=True,
                         help="Skip the 'shell-switching' block (launch menu + Ctrl+Shift+E "
                              "launcher). The default-shell wiring is unaffected. Windows only.")
    args = parser.parse_args()

    if not install_wezterm():
        return 1

    approach = args.shell_approach
    if approach is None and not args.skip_config:
        approach = prompt_shell_approach()

    if approach == "wsl":
        if IS_WINDOWS and not is_distro_installed(args.wsl_instance_name):
            print(f"\nHeads up: WSL instance '{args.wsl_instance_name}' isn't installed yet. "
                  "Writing the WezTerm config anyway, but it won't work until you create the "
                  "instance with ../wsl/install_wsl.py.")
    elif approach == "pwsh":
        if not install_pwsh():
            return 1

    if args.base and not args.skip_config:
        apply_base(args.config_file)

    if approach and not args.skip_config:
        apply_shell_approach(approach, args.wsl_instance_name, args.config_file)

    if IS_WINDOWS and args.shell_switching and not args.skip_config:
        wsl_distros = list_wsl_distros()
        apply_shell_switching(
            wsl_distros,
            pwsh_available=bool(find_pwsh_executable()),
            config_file=args.config_file,
        )

    step("Done. Restart WezTerm for changes to take effect.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
    except UnsupportedConfigFormatError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
