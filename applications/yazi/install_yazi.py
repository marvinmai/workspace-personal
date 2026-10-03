#!/usr/bin/env python3
"""
Installs Yazi and configures it, on Windows, Linux, and macOS:
  - installs yazi (winget on Windows, brew on macOS; on Linux/WSL via cargo,
    bootstrapping the Rust toolchain with rustup - and its build prerequisites -
    if they're missing, since yazi isn't in the default apt repos)
  - makes sure the `file` command yazi needs for mime-type detection is available
    (on Windows: locates Git for Windows' bundled file.exe and sets YAZI_FILE_ONE)
  - adds a "g v" keymap shortcut to a target path (the workspace root on Windows
    by default) in keymap.toml
  - installs a `y` shell wrapper function (PowerShell / Bash / Zsh) so exiting yazi leaves
    you in the directory you navigated to, per https://yazi-rs.github.io/docs/quick-start/
  - installs glow + the piper plugin and wires them up as the markdown previewer
    (on Windows: also makes sure `sh.exe` from Git for Windows is on PATH, since
    piper hard-codes `sh -c ...` with no way to point it at a different shell)
  - makes Enter on a markdown file open it rendered in glow's pager (the editor
    stays available via "O")

Safe to re-run: every step only adds what is missing, never overwrites an existing
valid YAZI_FILE_ONE, keymap binding, shell function, or previewer - it asks or skips.
"""

import argparse
import base64
import binascii
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

MARKER_BEGIN = "# >>> yazi-setup: {name} >>>"
MARKER_END = "# <<< yazi-setup: {name} <<<"
MANAGED_BEGIN_RE = re.compile(
    re.escape("# >>> yazi-setup: ") + r"(?P<name>.*?)" + re.escape(" >>>")
)
MANAGED_END_RE = re.compile(
    re.escape("# <<< yazi-setup: ") + r"(?P<name>.*?)" + re.escape(" <<<")
)
NEW_PROFILE_ENCODING = "utf-8-sig"
PROFILE_BOMS = {
    "utf-8-sig": codecs.BOM_UTF8,
    "utf-16-le": codecs.BOM_UTF16_LE,
    "utf-16-be": codecs.BOM_UTF16_BE,
    "utf-32-le": codecs.BOM_UTF32_LE,
    "utf-32-be": codecs.BOM_UTF32_BE,
}


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


def read_text_preserving_newlines(path):
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


def write_text_preserving_newlines(path, content):
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


def toml_basic_string(value):
    """Quote a value as a TOML basic string."""
    escapes = {
        "\b": r"\b",
        "\t": r"\t",
        "\n": r"\n",
        "\f": r"\f",
        "\r": r"\r",
        '"': '\\"',
        "\\": "\\\\",
    }
    output = []
    for char in str(value):
        codepoint = ord(char)
        if char in escapes:
            output.append(escapes[char])
        elif codepoint < 0x20 or 0x7F <= codepoint <= 0x9F:
            output.append(f"\\u{codepoint:04x}")
        else:
            output.append(char)
    return '"' + "".join(output) + '"'


class ProfileReadError(RuntimeError):
    """Raised when a PowerShell profile cannot be decoded or read."""


class ProfileDiscoveryError(ProfileReadError):
    """Raised when an installed PowerShell profile query fails."""


class ProfileMarkerError(ProfileReadError):
    """Raised when a managed Yazi marker pair is malformed."""


def profile_encoding(data):
    """Return the encoding implied by a BOM, or UTF-8 when none is present."""
    if data.startswith(codecs.BOM_UTF32_LE):
        return "utf-32-le"
    if data.startswith(codecs.BOM_UTF32_BE):
        return "utf-32-be"
    if data.startswith(codecs.BOM_UTF16_LE):
        return "utf-16-le"
    if data.startswith(codecs.BOM_UTF16_BE):
        return "utf-16-be"
    if data.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    return "utf-8"


def read_profile(path):
    """Read a PowerShell profile and return ``(text, encoding)``."""
    if not path.exists():
        return None

    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ProfileReadError(f"Could not read PowerShell profile '{path}': {exc}") from exc

    encoding = profile_encoding(data)
    bom = PROFILE_BOMS.get(encoding, b"")
    payload = data[len(bom):] if bom and data.startswith(bom) else data
    try:
        return payload.decode(encoding), encoding
    except UnicodeDecodeError as exc:
        raise ProfileReadError(
            f"Could not decode PowerShell profile '{path}'. Supported encodings are "
            "UTF-8 (with or without a BOM), UTF-16LE/BE with a BOM, and "
            "UTF-32LE/BE with a BOM."
        ) from exc


def write_profile(path, content, encoding):
    """Write profile text while preserving its detected encoding and line endings."""
    bom = PROFILE_BOMS.get(encoding, b"")
    codec = "utf-8" if encoding == "utf-8-sig" else encoding
    path.write_bytes(bom + content.encode(codec))


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
            raise ProfileMarkerError(
                f"Malformed Yazi markers in {path}: marker name is empty. "
                "No changes were written."
            )
        if kind == "begin":
            if active_name is not None:
                raise ProfileMarkerError(
                    f"Malformed Yazi markers in {path}: nested managed blocks are "
                    "not supported. No changes were written."
                )
            if name in completed:
                raise ProfileMarkerError(
                    f"Malformed Yazi markers in {path}: managed block '{name}' "
                    "appears more than once. No changes were written."
                )
            active_name = name
        elif active_name is None:
            raise ProfileMarkerError(
                f"Malformed Yazi markers in {path}: end marker '{name}' has no "
                "matching begin marker. No changes were written."
            )
        elif active_name != name:
            raise ProfileMarkerError(
                f"Malformed Yazi markers in {path}: end marker '{name}' does not "
                f"match active block '{active_name}'. No changes were written."
            )
        else:
            completed.add(active_name)
            active_name = None

    if active_name is not None:
        raise ProfileMarkerError(
            f"Malformed Yazi markers in {path}: begin marker '{active_name}' has "
            "no matching end marker. No changes were written."
        )


def preflight_marker_paths(paths, profile_paths=()):
    """Read and validate every managed-marker target before any write."""
    seen = set()
    profile_keys = {
        os.path.normcase(str(Path(raw_path)))
        for raw_path in profile_paths
    }
    for raw_path in paths:
        path = Path(raw_path)
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        if path.exists() and not path.is_file():
            raise ProfileReadError(
                f"Yazi configuration path '{path}' exists but is not a file. "
                "No changes were written."
            )
        if path.exists():
            if key in profile_keys:
                existing_profile = read_profile(path)
                content = existing_profile[0] if existing_profile is not None else ""
            else:
                content = read_text_preserving_newlines(path)
            validate_managed_markers(content, path)


def shell_wrapper_paths():
    if IS_WINDOWS:
        return powershell_profile_paths()
    return [Path.home() / ".bashrc", Path.home() / ".zshrc"]


def install_package(display_name, winget_id=None, brew_pkg=None, pacman_pkg=None,
                     apt_pkg=None, dnf_pkg=None, manual_hint=None):
    step(f"Installing {display_name}")

    command = None
    if IS_WINDOWS and winget_id and shutil.which("winget"):
        command = ["winget", "install", "--id", winget_id, "-e",
                   "--accept-package-agreements", "--accept-source-agreements"]
    elif brew_pkg and shutil.which("brew"):
        command = ["brew", "install", brew_pkg]
    elif IS_LINUX and pacman_pkg and shutil.which("pacman"):
        command = ["sudo", "pacman", "-S", "--needed", "--noconfirm", pacman_pkg]
    elif IS_LINUX and apt_pkg and shutil.which("apt-get"):
        command = ["sudo", "apt-get", "install", "-y", apt_pkg]
    elif IS_LINUX and dnf_pkg and shutil.which("dnf"):
        command = ["sudo", "dnf", "install", "-y", dnf_pkg]

    if command:
        result = run(command)
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


def merge_marker_block(path, name, block, create_if_missing=True, conflict_pattern=None,
                        conflict_label=None, profile=False, scan_paths=()):
    """Append `block` wrapped in begin/end markers to `path`, unless already present.

    If `conflict_pattern` matches existing content outside the marker block, warns and
    skips instead of appending, so a user's own customization is never clobbered.
    """
    begin = MARKER_BEGIN.format(name=name)
    end = MARKER_END.format(name=name)

    existing_profile = read_profile(path)
    if existing_profile is None:
        content = ""
        encoding = NEW_PROFILE_ENCODING if profile else "utf-8"
    else:
        content, encoding = existing_profile

    validate_managed_markers(content, path)
    newline = config_newline(content)
    normalized_block = re.sub(r"\r\n|\r|\n", newline, block)
    new_block = f"{begin}{newline}{normalized_block}{newline}{end}"

    marker_re = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)
    if marker_re.search(content):
        updated = marker_re.sub(lambda _match: new_block, content, count=1)
        if updated != content:
            write_profile(path, updated, encoding)
            print(f"Updated '{name}' in {path}.")
        else:
            print(f"'{name}' is already up to date in {path}.")
        return

    if conflict_pattern:
        marker_re = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)
        for candidate in [path, *scan_paths]:
            candidate_profile = read_profile(candidate)
            if candidate_profile is None:
                continue
            existing, _candidate_encoding = candidate_profile
            if marker_re.search(existing):
                continue
            if re.search(conflict_pattern, existing, re.MULTILINE):
                label = conflict_label or name
                print(f"{label} already defined in {candidate} - leaving {path} untouched.")
                return

    if not path.exists() and not create_if_missing:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    write_profile(path, f"{content}\n{new_block}\n", encoding)
    print(f"Installed '{name}' into {path}.")


# --- 1. Install yazi -------------------------------------------------------

def find_yazi_executable():
    for name in ("yazi", "yazi.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def add_cargo_bin_to_path():
    """Prepend ~/.cargo/bin to this process's PATH so a freshly cargo-installed
    yazi/ya (and cargo itself, after a rustup bootstrap) is found by the later
    shutil.which() lookups in this same run - rustup only wires it into future
    shells, not the current process."""
    cargo_bin = str(Path.home() / ".cargo" / "bin")
    if cargo_bin not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = cargo_bin + os.pathsep + os.environ.get("PATH", "")


def ensure_cargo():
    """Return a usable `cargo` command, bootstrapping the Rust toolchain via
    rustup if it's missing. On Linux this also installs the build prerequisites
    (a C compiler + curl) that a bare distro like a fresh WSL Ubuntu lacks.
    Returns None if cargo still isn't available afterwards."""
    add_cargo_bin_to_path()
    found = shutil.which("cargo")
    if found:
        return found

    step("Installing the Rust toolchain (cargo) via rustup")

    if IS_WINDOWS:
        if not install_package(
            "Rustup",
            winget_id="Rustlang.Rustup",
            manual_hint="Install it from https://rustup.rs",
        ):
            return None
    elif IS_MAC:
        result = run(["sh", "-c",
                      "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y"])
        if result.returncode != 0:
            print(f"Rustup installation failed with exit code {result.returncode}.")
            return None
    else:
        # A bare Linux install (fresh WSL Ubuntu) has neither curl nor a C linker;
        # cargo needs both to build yazi from source.
        prerequisites_ok = True
        if shutil.which("apt-get"):
            for command in (
                ["sudo", "apt-get", "update", "-qq"],
                ["sudo", "apt-get", "install", "-y", "curl", "build-essential", "pkg-config"],
            ):
                result = run(command)
                if result.returncode != 0:
                    prerequisites_ok = False
                    print(f"Build prerequisite command failed with exit code "
                          f"{result.returncode}.")
        elif shutil.which("pacman"):
            result = run(
                ["sudo", "pacman", "-S", "--needed", "--noconfirm",
                 "curl", "base-devel", "pkgconf"]
            )
            prerequisites_ok = result.returncode == 0
        elif shutil.which("dnf"):
            result = run(
                ["sudo", "dnf", "install", "-y",
                 "curl", "gcc", "make", "pkgconf-pkg-config"]
            )
            prerequisites_ok = result.returncode == 0
        else:
            prerequisites_ok = False
            print("No supported Linux package manager was found for Rust build prerequisites.")

        if not prerequisites_ok:
            return None

        result = run(["sh", "-c",
                      "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y"])
        if result.returncode != 0:
            print(f"Rustup installation failed with exit code {result.returncode}.")
            return None

    add_cargo_bin_to_path()
    return shutil.which("cargo")


def install_yazi():
    step("Installing yazi")

    existing = find_yazi_executable()
    if existing:
        print(f"Yazi is already installed at: {existing}")
        return True

    # Windows and macOS ship reliable prebuilt yazi packages - use them directly.
    if IS_WINDOWS and shutil.which("winget"):
        result = run(["winget", "install", "--id", "sxyazi.yazi", "-e",
                      "--accept-package-agreements", "--accept-source-agreements"])
        if result.returncode == 0:
            return True
        print(f"Yazi installation failed with exit code {result.returncode}; "
              "trying the source-install path.")
    if IS_MAC and shutil.which("brew"):
        result = run(["brew", "install", "yazi"])
        if result.returncode == 0:
            return True
        print(f"Yazi installation failed with exit code {result.returncode}; "
              "trying the source-install path.")

    # Elsewhere (notably Linux/WSL, where yazi isn't in the default apt repos)
    # build from source with cargo - yazi's recommended cross-distro path.
    cargo = ensure_cargo()
    if not cargo:
        print("Could not install a Rust toolchain automatically. Install cargo "
              "(https://rustup.rs), then run: cargo install --locked yazi-fm yazi-cli")
        return False

    result = run([cargo, "install", "--locked", "yazi-fm", "yazi-cli"])
    if result.returncode != 0:
        print(f"Yazi source installation failed with exit code {result.returncode}.")
        return False
    return True


# --- 2. Make sure `file` is available for mime-type detection -------------

def find_git_usr_bin():
    """Locate Git for Windows' usr/bin dir (has file.exe, sh.exe, etc.)."""
    candidates = []

    git = shutil.which("git")
    if git:
        git_path = Path(git)
        level1 = git_path.parent               # .../Git/cmd  or  .../Git/mingw64/bin
        level2 = level1.parent                  # .../Git      or  .../Git/mingw64
        candidates += [
            level1 / "usr" / "bin",
            level2 / "usr" / "bin",
            level2.parent / "usr" / "bin",
        ]

    candidates += [
        Path("C:/Program Files/Git/usr/bin"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Git/usr/bin",
        Path(os.environ.get("USERPROFILE", "")) / "scoop/apps/git/current/usr/bin",
    ]

    for c in candidates:
        if c and c.is_dir():
            return c.resolve()
    return None


def find_windows_file_exe():
    bin_dir = find_git_usr_bin()
    if bin_dir and (bin_dir / "file.exe").is_file():
        return str(bin_dir / "file.exe")
    return None


def set_windows_user_env(name, value):
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE)
    try:
        winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)
    finally:
        winreg.CloseKey(key)
    if name.lower() == "path":
        current_entries = [
            entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry
        ]
        for entry in str(value).split(";"):
            if entry and not any(
                os.path.normcase(existing) == os.path.normcase(entry)
                for existing in current_entries
            ):
                current_entries.append(entry)
        process_value = os.pathsep.join(current_entries)
        os.environ["PATH"] = process_value
        os.environ["Path"] = process_value
    else:
        os.environ[name] = value
    broadcast_windows_environment_change()


def broadcast_windows_environment_change():
    """Notify long-lived Windows processes that HKCU environment values changed."""
    if not IS_WINDOWS:
        return

    import ctypes
    from ctypes import wintypes

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        send_message_timeout = user32.SendMessageTimeoutW
        send_message_timeout.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(wintypes.DWORD),
        ]
        send_message_timeout.restype = wintypes.LPARAM
        result = wintypes.DWORD()
        environment = ctypes.c_wchar_p("Environment")
        environment_pointer = ctypes.cast(environment, ctypes.c_void_p).value
        sent = send_message_timeout(
            wintypes.HWND(0xFFFF),
            wintypes.UINT(0x001A),
            wintypes.WPARAM(0),
            wintypes.LPARAM(environment_pointer),
            wintypes.UINT(0x0002),
            wintypes.UINT(5000),
            ctypes.byref(result),
        )
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise RuntimeError(
            "Could not broadcast the Windows environment change via user32."
        ) from exc

    if not sent:
        error = ctypes.get_last_error()
        detail = f" (GetLastError={error})" if error else ""
        raise RuntimeError(
            "Windows environment update was written, but WM_SETTINGCHANGE "
            f"could not be broadcast{detail}."
        )


def get_windows_user_path():
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ)
    try:
        value, _ = winreg.QueryValueEx(key, "Path")
        return value
    except FileNotFoundError:
        return ""
    finally:
        winreg.CloseKey(key)


def configure_file_command():
    step("Configuring mime-type detection (the `file` command)")

    if not IS_WINDOWS:
        if shutil.which("file"):
            print("`file` is already available on PATH - nothing to do.")
            return True
        else:
            print("`file` was not found on PATH.")
            if IS_MAC:
                print("Install it with: brew install file")
            else:
                print("Install it with your package manager, e.g.: sudo apt-get install file")
            return False

    existing = os.environ.get("YAZI_FILE_ONE")
    if existing and Path(existing).is_file():
        print(f"YAZI_FILE_ONE is already set and valid: {existing} (leaving as is)")
        return True

    found = find_windows_file_exe()

    if not found:
        print("Could not auto-detect file.exe (normally shipped inside Git for Windows,")
        print(r"under <Git install>\usr\bin\file.exe).")
        manual = ask("Enter full path to file.exe (or leave blank to skip): ").strip()
        if manual and Path(manual).is_file():
            found = manual
        elif manual:
            print("Path not found, skipping YAZI_FILE_ONE setup.")
            return False

    if not found:
        return True

    if existing and existing != found:
        answer = ask(f"YAZI_FILE_ONE is currently set to '{existing}' (missing/invalid). "
                     f"Replace with '{found}'? [y/N] ")
        if not answer.strip().lower().startswith("y"):
            return True

    set_windows_user_env("YAZI_FILE_ONE", found)
    print(f"Set YAZI_FILE_ONE = {found} (restart your terminal to pick it up)")
    return True


# --- 3. Add "g v" keymap shortcut ------------------------------------------

def yazi_config_dir():
    override = os.environ.get("YAZI_CONFIG_HOME")
    if override:
        return Path(override)
    if IS_WINDOWS:
        return Path(os.environ["APPDATA"]) / "yazi" / "config"
    return Path.home() / ".config" / "yazi"


def configure_keymap_shortcut(keys, target_path, desc, config_dir=None):
    chord = " ".join(keys)
    step(f"Configuring '{chord}' shortcut to {target_path}")

    keymap_path = (Path(config_dir) if config_dir else yazi_config_dir()) / "keymap.toml"
    keymap_path.parent.mkdir(parents=True, exist_ok=True)

    content = read_text_preserving_newlines(keymap_path) if keymap_path.exists() else ""
    newline = config_newline(content)
    on = ", ".join(toml_basic_string(k) for k in keys)
    run_value = toml_basic_string("cd " + str(target_path))
    block = (
        newline.join(
            (
                "[[mgr.prepend_keymap]]",
                f"on   = [{on}]",
                f"run  = {run_value}",
                f"desc = {toml_basic_string(desc)}",
            )
        )
    )

    key_re = (
        r"on\s*=\s*\[\s*"
        + r"\s*,\s*".join(re.escape(toml_basic_string(k)) for k in keys)
        + r"\s*\]"
    )
    if re.search(key_re, content):
        print(f"keymap.toml already has a '{chord}' binding - leaving it untouched. "
              f"Edit {keymap_path} by hand if you want it to point elsewhere.")
        return

    with keymap_path.open("a", encoding="utf-8", newline="") as stream:
        stream.write(f"{newline}{block}{newline}")
    print(f"Appended '{chord}' -> {target_path} shortcut to {keymap_path}")


# --- 4. "y" shell wrapper so exiting yazi cd's the terminal ----------------

POWERSHELL_Y_FUNCTION = """function y {
\t$tmp = (New-TemporaryFile).FullName
\tyazi.exe @args --cwd-file="$tmp"
\t$cwd = Get-Content -Path $tmp -Encoding UTF8
\tif ($cwd -and $cwd -ne $PWD.Path -and (Test-Path -LiteralPath $cwd -PathType Container)) {
\t\tSet-Location -LiteralPath (Resolve-Path -LiteralPath $cwd).Path
\t}
\tRemove-Item -Path $tmp
}"""

POSIX_Y_FUNCTION = """function y() {
\tlocal tmp="$(mktemp -t "yazi-cwd.XXXXXX")" cwd
\tcommand yazi "$@" --cwd-file="$tmp"
\tIFS= read -r -d '' cwd < "$tmp"
\t[ "$cwd" != "$PWD" ] && [ -d "$cwd" ] && builtin cd -- "$cwd"
\tcommand rm -f -- "$tmp"
}"""

POWERSHELL_Y_CONFLICT_PATTERN = (
    r"(?im)^\s*(?:"
    r"function\s+(?:[A-Za-z_][\w]*:)?y\b"
    r"|(?:Set|New)-Alias\s+['\"]?y['\"]?(?=\s|$)"
    r"|(?:Set|New)-Alias\b[^\r\n]*?(?<!\S)-Name"
    r"(?:\s+|[:=]\s*)['\"]?y['\"]?(?=\s|$)"
    r")"
)
POSIX_Y_CONFLICT_PATTERN = r"(?im)^\s*(?:function\s+y\b|y\s*\(\s*\)|alias\s+y\s*=)"


PROFILE_QUERY = (
    "$paths = @($PROFILE.CurrentUserAllHosts, $PROFILE.CurrentUserCurrentHost, "
    "$PROFILE.AllUsersAllHosts, $PROFILE.AllUsersCurrentHost); "
    "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($paths -join [char]10)))"
)


def _decode_profile_paths(raw):
    """Decode the ASCII/Base64 profile response from PowerShell."""
    try:
        encoded = (raw or b"").strip()
        if isinstance(encoded, str):
            encoded = encoded.encode("ascii")
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (UnicodeError, ValueError, binascii.Error) as exc:
        raise ProfileDiscoveryError(
            "PowerShell profile discovery returned malformed Base64/UTF-8 data."
        ) from exc

    lines = decoded.split("\n")
    if len(lines) != 4:
        raise ProfileDiscoveryError(
            f"PowerShell profile discovery returned {len(lines)} paths; expected exactly four."
        )

    paths = []
    seen = set()
    for line in lines:
        if line.endswith("\r"):
            line = line[:-1]
        if not line or line.isspace() or "\r" in line or "\x00" in line:
            raise ProfileDiscoveryError(
                "PowerShell profile discovery returned an empty or invalid path."
            )
        try:
            path = Path(line)
            if not path.is_absolute():
                raise ValueError("path is not rooted")
            full_path = path.resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise ProfileDiscoveryError(
                f"PowerShell profile discovery returned an invalid path: {line!r}."
            ) from exc
        key = os.path.normcase(str(full_path))
        if key in seen:
            raise ProfileDiscoveryError(
                "PowerShell profile discovery returned duplicate profile paths."
            )
        seen.add(key)
        paths.append(path)
    return paths


def _query_powershell_profile_paths(executable):
    try:
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", PROFILE_QUERY],
            capture_output=True, text=False, timeout=15,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProfileDiscoveryError(
            f"PowerShell profile discovery timed out for '{executable}'."
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProfileDiscoveryError(
            f"PowerShell profile discovery failed for '{executable}': {exc}"
        ) from exc
    if result.returncode != 0:
        raise ProfileDiscoveryError(
            f"PowerShell profile discovery failed for '{executable}' with exit code "
            f"{result.returncode}."
        )
    return _decode_profile_paths(result.stdout)


def _powershell_profile_path_sets():
    """Resolve writable targets and every profile to scan for conflicts."""
    targets = []
    scan_paths = []
    interpreter_found = False
    for exe in ("powershell", "pwsh"):
        found = shutil.which(exe)
        if not found:
            continue
        interpreter_found = True
        paths = _query_powershell_profile_paths(found)
        targets.append(paths[0])
        scan_paths.extend(paths)

    def unique_paths(paths):
        seen, unique = set(), []
        for path in paths:
            key = os.path.normcase(str(path))
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        return unique

    if not interpreter_found:
        docs = Path.home() / "Documents"
        fallback = [
            docs / "WindowsPowerShell" / "profile.ps1",
            docs / "PowerShell" / "profile.ps1",
        ]
        return fallback, fallback

    return unique_paths(targets), unique_paths(scan_paths)


def powershell_profile_paths():
    """Return CurrentUserAllHosts profiles for installed PowerShell editions."""
    targets, _scan_paths = _powershell_profile_path_sets()
    return targets


def powershell_profile_scan_paths():
    """Return every loaded profile from each installed PowerShell edition."""
    _targets, scan_paths = _powershell_profile_path_sets()
    return scan_paths


def configure_cd_on_exit(profiles=None, scan_paths=None):
    step("Configuring 'y' shell wrapper (cd to last directory on exit)")

    profiles = profiles if profiles is not None else shell_wrapper_paths()
    scan_paths = scan_paths if scan_paths is not None else profiles
    marker_paths = [*profiles, *scan_paths]
    preflight_marker_paths(
        marker_paths,
        profile_paths=marker_paths if IS_WINDOWS else (),
    )
    if IS_WINDOWS:
        for profile in profiles:
            merge_marker_block(
                profile, "y function", POWERSHELL_Y_FUNCTION,
                conflict_pattern=POWERSHELL_Y_CONFLICT_PATTERN,
                conflict_label="A 'y' function or alias",
                profile=True, scan_paths=scan_paths,
            )
    else:
        for rc in profiles:
            merge_marker_block(
                rc, "y function", POSIX_Y_FUNCTION,
                conflict_pattern=POSIX_Y_CONFLICT_PATTERN,
                conflict_label="A 'y' function or alias",
            )


# --- 5. Markdown preview via glow (through the piper plugin) --------------

PIPER_MD_PREVIEWER = (
    '[[plugin.prepend_previewers]]\n'
    'url = "*.md"\n'
    'run = \'piper -- CLICOLOR_FORCE=1 glow -w=$w -s=dark "$1"\''
)

# Enter renders markdown in glow's pager; "O" still offers the editor.
GLOW_MD_OPENER = (
    # An array-of-tables header stays valid TOML even if the user already has
    # an [opener] table elsewhere in the file.
    '[[opener.markdown]]\n'
    'run = "glow -p -s=dark %s1"\n'
    'block = true\n'
    'desc = "Render (glow)"\n'
    '\n'
    '[[open.prepend_rules]]\n'
    'url = "*.md"\n'
    'use = ["markdown", "edit", "reveal"]'
)
MARKDOWN_OPENER_CONFLICT_PATTERN = (
    r'^\s*markdown\s*=|^\s*\[\[?\s*opener\.markdown'
    r'|^\s*\[\[open\.prepend_rules\]\]\s*\n\s*url\s*=\s*"\*\.md"'
)


def configure_markdown_opener(yazi_toml):
    upsert_marker_block(
        yazi_toml,
        "markdown-open",
        GLOW_MD_OPENER,
        conflict_pattern=MARKDOWN_OPENER_CONFLICT_PATTERN,
        conflict_label="A manual markdown opener",
    )


def upsert_marker_block(path, name, block, conflict_pattern=None, conflict_label=None):
    """Create or update a managed TOML block without touching other settings."""
    begin = MARKER_BEGIN.format(name=name)
    end = MARKER_END.format(name=name)
    content = read_text_preserving_newlines(path) if path.exists() else ""
    validate_managed_markers(content, path)
    newline = config_newline(content)
    normalized_block = re.sub(r"\r\n|\r|\n", newline, block)
    new_block = f"{begin}{newline}{normalized_block}{newline}{end}"
    marker_re = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)

    if marker_re.search(content):
        updated = marker_re.sub(lambda _m: new_block, content, count=1)
        if updated != content:
            write_text_preserving_newlines(path, updated)
            print(f"Updated '{name}' in {path}.")
        else:
            print(f"'{name}' is already up to date in {path}.")
        return

    if conflict_pattern and re.search(conflict_pattern, content, re.MULTILINE):
        label = conflict_label or name
        print(f"{label} already exists in {path} - leaving it untouched.")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    if content.strip():
        prefix = content
        if not content.endswith(newline):
            prefix += newline
        if not prefix.endswith(newline * 2):
            prefix += newline
    else:
        prefix = ""
    write_text_preserving_newlines(path, prefix + new_block + newline)
    print(f"Installed '{name}' into {path}.")


def ensure_sh_on_path():
    """piper hard-codes `Command("sh"):arg("-c", ...)` with no way to configure a
    different shell, so on Windows `sh.exe` (shipped in Git for Windows) must be
    reachable via PATH - just pointing YAZI_FILE_ONE at it is not enough."""
    step("Making sure `sh` is on PATH (piper previewer needs it)")

    if not IS_WINDOWS:
        if shutil.which("sh"):
            print("`sh` is already available on PATH - nothing to do.")
            return True
        else:
            print("`sh` was not found on PATH - install your distro's `sh`/POSIX shell package.")
            return False

    if shutil.which("sh"):
        print("`sh` is already on PATH - nothing to do.")
        return True

    bin_dir = find_git_usr_bin()
    if not bin_dir or not (bin_dir / "sh.exe").is_file():
        print("Could not find sh.exe (normally shipped inside Git for Windows, under "
              r"<Git install>\usr\bin\sh.exe). Markdown preview will keep failing with "
              "'sh: program not found' until sh is on PATH.")
        return False

    current = get_windows_user_path()
    entries = [e for e in current.split(";") if e.strip()]
    for e in entries:
        try:
            if Path(e).resolve() == bin_dir:
                print(f"{bin_dir} is already in your user PATH (restart your terminal "
                      "to pick it up).")
                return True
        except OSError:
            continue

    answer = ask(f"Append '{bin_dir}' to your user PATH so `sh` (needed by piper) is "
                 f"found? [y/N] ")
    if not answer.strip().lower().startswith("y"):
        print("Skipping markdown preview setup because `sh` was not added to PATH.")
        return None

    new_path = current + (";" if current and not current.endswith(";") else "") + str(bin_dir)
    set_windows_user_env("Path", new_path)
    print(f"Appended {bin_dir} to your user PATH (restart your terminal to pick it up)")
    return True


# Newer piper commits declare `@since` a yazi release newer than the crates.io one
# and show "yazi version too old" instead of a preview. This is the last commit
# that supports yazi 26.1.22+ (including dark/light mode).
PIPER_REV = "b9946d9"
PIPER_DEP_RE = re.compile(
    r'\[\[plugin\.deps\]\]\s*\n(?:(?!\[).*\n)*?use\s*=\s*"yazi-rs/plugins:piper"[^\n]*\n'
    r'(?:(?!\[)[^\n]*\n?)*'
)


def pin_piper_plugin(package_toml):
    """Make package.toml pin piper to PIPER_REV (dropping any stale hash)."""
    content = read_text_preserving_newlines(package_toml) if package_toml.exists() else ""
    newline = config_newline(content)
    entry = newline.join((
        "[[plugin.deps]]",
        'use = "yazi-rs/plugins:piper"',
        f'rev = "{PIPER_REV}"',
    )) + newline
    if PIPER_DEP_RE.search(content):
        updated = PIPER_DEP_RE.sub(lambda _m: entry + newline, content, count=1)
    else:
        updated = entry + newline + content
    if updated != content:
        package_toml.parent.mkdir(parents=True, exist_ok=True)
        write_text_preserving_newlines(package_toml, updated)
        print(f"Pinned piper to {PIPER_REV} in {package_toml}.")
    return updated != content


CHARM_APT_SETUP = (
    "sudo mkdir -p /etc/apt/keyrings && "
    "curl -fsSL https://repo.charm.sh/apt/gpg.key "
    "| sudo gpg --dearmor --yes -o /etc/apt/keyrings/charm.gpg && "
    "echo 'deb [signed-by=/etc/apt/keyrings/charm.gpg] https://repo.charm.sh/apt/ * *' "
    "| sudo tee /etc/apt/sources.list.d/charm.list >/dev/null && "
    "sudo apt-get update -qq"
)


def ensure_charm_apt_repo():
    """glow isn't in the default Debian/Ubuntu repos; add Charm's apt repo first."""
    if not (IS_LINUX and shutil.which("apt-get")) or shutil.which("brew"):
        return True
    if Path("/etc/apt/sources.list.d/charm.list").exists():
        return True
    step("Adding the Charm apt repository (provides glow)")
    result = run(["sh", "-c", CHARM_APT_SETUP])
    if result.returncode != 0:
        print(f"Could not add the Charm apt repo (exit code {result.returncode}).")
        return False
    return True


def configure_markdown_preview(config_dir=None):
    shell_status = ensure_sh_on_path()
    if shell_status is None:
        return True
    if not shell_status:
        return False

    if not shutil.which("glow") and not ensure_charm_apt_repo():
        return False

    if shutil.which("glow"):
        print("glow is already installed - skipping.")
    elif not install_package(
        "glow",
        winget_id="charmbracelet.glow",
        brew_pkg="glow",
        pacman_pkg="glow",
        apt_pkg="glow",
        dnf_pkg="glow",
        manual_hint=(
            "Debian/Ubuntu needs the Charm apt repo first - see "
            "https://github.com/charmbracelet/glow#installation"
        ),
    ):
        return False

    step("Installing the piper previewer plugin")
    if not shutil.which("ya"):
        print("`ya` (yazi's package manager CLI) was not found on PATH - "
              "install yazi first, then re-run this script.")
        return False
    config_path = Path(config_dir) if config_dir else yazi_config_dir()
    repinned = pin_piper_plugin(config_path / "package.toml")
    # Switching revs makes the old checkout's hash stale, which yazi reports as
    # "local modifications"; --discard replaces it with the pinned revision.
    result = run(["ya", "pkg", "install", *(["--discard"] if repinned else [])],
                 env={**os.environ, "YAZI_CONFIG_HOME": str(config_path)})
    if result.returncode != 0:
        print(f"Could not install the piper plugin (exit code {result.returncode}). "
              "Re-run after resolving the yazi package-manager error.")
        return False

    step("Wiring glow into yazi.toml as the markdown previewer")
    yazi_toml = (Path(config_dir) if config_dir else yazi_config_dir()) / "yazi.toml"
    upsert_marker_block(
        yazi_toml,
        "markdown-preview",
        PIPER_MD_PREVIEWER,
        conflict_pattern=r'url\s*=\s*"\*\.md"',
        conflict_label="A manual '*.md' previewer",
    )

    step("Making yazi open markdown files rendered in glow")
    configure_markdown_opener(yazi_toml)
    return True


# --- main -------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default_target = (
        os.environ.get("WORKSPACE_PERSONAL_YAZI_TARGET")
        or (str(Path(__file__).resolve().parents[2]) if IS_WINDOWS else None)
    )
    parser.add_argument(
        "--target-path",
        default=default_target,
        help="Path the 'g v' shortcut should jump to (default: the workspace root "
             "on Windows). "
             "Required on Linux/macOS since there is no drive-letter equivalent.",
    )
    parser.add_argument("--config-dir", default=None,
                        help="Yazi config directory (overrides YAZI_CONFIG_HOME and "
                             "the platform default).")
    parser.add_argument("--extra-shortcut", nargs=2, action="append", default=[],
                         metavar=("KEYS", "PATH"),
                         help="Additional 'cd' keymap shortcut, e.g. "
                              "--extra-shortcut 'g d' ~/workspace (repeatable).")
    parser.add_argument("--skip-shell-wrapper", action="store_true",
                         help="Don't install the 'y' cd-on-exit shell function.")
    parser.add_argument("--skip-markdown-preview", action="store_true",
                         help="Don't install glow / the piper markdown previewer "
                              "and glow markdown opener.")
    args = parser.parse_args()

    wrapper_paths = None
    profile_scan_paths = None
    marker_paths = []
    if not args.skip_shell_wrapper:
        wrapper_paths = shell_wrapper_paths()
        profile_scan_paths = (
            powershell_profile_scan_paths() if IS_WINDOWS else wrapper_paths
        )
        marker_paths.extend([*wrapper_paths, *profile_scan_paths])
    if not args.skip_markdown_preview:
        config_dir = Path(args.config_dir) if args.config_dir else yazi_config_dir()
        marker_paths.append(config_dir / "yazi.toml")
    preflight_marker_paths(
        marker_paths,
        profile_paths=profile_scan_paths if IS_WINDOWS and profile_scan_paths else (),
    )

    if not install_yazi():
        return 1
    if not configure_file_command():
        return 1

    if args.target_path:
        configure_keymap_shortcut(
            ["g", "v"], args.target_path, "Go to target shortcut", args.config_dir)
    else:
        step("Skipping 'g v' shortcut")
        print("No --target-path given and no Windows default applies. "
              "Re-run with --target-path /path/you/want to add the shortcut.")

    for keys_str, path in args.extra_shortcut:
        configure_keymap_shortcut(
            keys_str.split(), path, f"Go to {path}", args.config_dir)

    if not args.skip_shell_wrapper:
        configure_cd_on_exit(wrapper_paths, profile_scan_paths)

    if not args.skip_markdown_preview:
        if not configure_markdown_preview(args.config_dir):
            return 1

    step("Done. Restart your terminal (and yazi) for changes to take effect.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ProfileReadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
