#!/usr/bin/env python3
"""
Installs PowerShell 7 (pwsh) and configures the current user's PowerShell profile
on Windows (and, where it applies, Linux/macOS):
  - installs pwsh via the platform's package manager (winget / brew) if missing
  - prompts for a default start directory and wires a managed profile block that
    Set-Location's there ONLY from a "fresh" launch (home or System32), so an
    "open PowerShell here" keeps its own directory

Why the start-directory lives in the profile: WezTerm's shell-switcher launches
`pwsh.exe -NoLogo`, which still loads the profile (only -NoProfile skips it). So
the same Set-Location that a plain `pwsh` honours is what makes the WezTerm
PowerShell tab open in the configured directory too - no WezTerm config needed.

The personal shell commands (`ai`, `ll`, `np`, ...) are not installed here;
they come from `scripts/setup-shell-extras.ps1`, which is their single
installer.

Safe to re-run: each managed block is replaced in place, and anything you added to
the profile by hand, outside those blocks, is left untouched. Target profiles are
the CurrentUserAllHosts profile of every installed edition (Windows PowerShell 5.x
and PowerShell 7), resolved by asking each interpreter for $PROFILE so OneDrive
Documents redirection is handled correctly. A fresh Winget install is located
explicitly before profile discovery because the current process PATH is stale.
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

MARKER_BEGIN = "# >>> powershell-setup: {name} >>>"
MARKER_END = "# <<< powershell-setup: {name} <<<"
MANAGED_BEGIN_RE = re.compile(
    re.escape("# >>> powershell-setup: ") + r"(.*?)" + re.escape(" >>>")
)
MANAGED_END_RE = re.compile(
    re.escape("# <<< powershell-setup: ") + r"(.*?)" + re.escape(" <<<")
)

# New profiles are written with a BOM: Windows PowerShell 5.x reads a BOM-less
# UTF-8 profile as the legacy ANSI codepage, so a BOM keeps non-ASCII paths intact.
NEW_PROFILE_ENCODING = "utf-8-sig"
PROFILE_BOMS = {
    "utf-8-sig": codecs.BOM_UTF8,
    "utf-16-le": codecs.BOM_UTF16_LE,
    "utf-16-be": codecs.BOM_UTF16_BE,
    "utf-32-le": codecs.BOM_UTF32_LE,
    "utf-32-be": codecs.BOM_UTF32_BE,
}

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_START_DIR = os.environ.get("WORKSPACE_PERSONAL_START_DIR", str(WORKSPACE_ROOT))


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


# --- 1. Install PowerShell 7 --------------------------------------------------

def install_package(display_name, winget_id=None, brew_cask=None, manual_hint=None):
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

    print(f"Could not find a supported package manager for {display_name}.")
    if manual_hint:
        print(manual_hint)
    print("The requested installation was not completed.")
    return False


def find_pwsh_executable():
    found = shutil.which("pwsh")
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
              "could not be located. Profile configuration was not attempted.")
        return False
    add_executable_to_path(installed)
    print(f"Found newly installed pwsh at: {installed}")
    return True


# --- 2. Resolve the profile files to edit -------------------------------------

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


def profile_paths(interpreter, executable=None):
    """Ask an installed interpreter for all four `$PROFILE` paths."""
    exe = executable or shutil.which(interpreter)
    if not exe:
        return []
    try:
        result = subprocess.run(
            [str(exe), "-NoProfile", "-NonInteractive", "-Command",
             PROFILE_QUERY],
            capture_output=True, text=False, timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProfileDiscoveryError(
            f"PowerShell profile discovery timed out for '{exe}'."
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProfileDiscoveryError(
            f"PowerShell profile discovery failed for '{exe}': {exc}"
        ) from exc
    if result.returncode != 0:
        raise ProfileDiscoveryError(
            f"PowerShell profile discovery failed for '{exe}' with exit code "
            f"{result.returncode}."
        )
    return _decode_profile_paths(result.stdout)


def profile_all_hosts_path(interpreter, executable=None):
    """Ask `interpreter` for its CurrentUserAllHosts profile path."""
    paths = profile_paths(interpreter, executable=executable)
    return paths[0] if paths else None


def _unique_paths(paths):
    seen = set()
    unique = []
    for path in paths:
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def resolve_profile_paths(pwsh_executable=None):
    """Return CurrentUserAllHosts targets and all profiles to scan."""
    targets = []
    scan_paths = []
    for interpreter in ("pwsh", "powershell"):
        executable = pwsh_executable if interpreter == "pwsh" else None
        paths = profile_paths(interpreter, executable=executable)
        if interpreter == "pwsh" and pwsh_executable and not paths:
            raise ProfileDiscoveryError(
                f"Could not resolve the PowerShell 7 profile using '{pwsh_executable}'."
            )
        if paths:
            targets.append(paths[0])
            scan_paths.extend(paths)
    return _unique_paths(targets), _unique_paths(scan_paths)


def resolve_profile_targets(pwsh_executable=None):
    """Return the CurrentUserAllHosts profile of each installed edition."""
    targets, _scan_paths = resolve_profile_paths(pwsh_executable=pwsh_executable)
    return targets


def sibling_scan_paths(targets):
    """Host-specific profiles next to each target - scanned (but not written) so a
    conflicting command defined in, say, Microsoft.PowerShell_profile.ps1 is seen."""
    host_profiles = (
        "Microsoft.PowerShell_profile.ps1",
        "Microsoft.PowerShellISE_profile.ps1",
        "Microsoft.VSCode_profile.ps1",
    )
    return [t.parent / name for t in targets for name in host_profiles]


# --- 3. Managed profile blocks ------------------------------------------------

def ps_single_quote(value):
    """Escape a string for a PowerShell single-quoted literal."""
    return value.replace("'", "''")


def start_directory_body(target_dir):
    escaped = ps_single_quote(target_dir)
    # $env:SystemRoot is empty off Windows, so the System32 paths are added only
    # when it's set - calling Join-Path with a null root would throw and break the
    # profile on Linux/macOS. There, a "fresh" launch is just $HOME.
    return (
        '# Jump to the configured working directory, but only from a "fresh" launch\n'
        '# (home or System32) so an "open PowerShell here" keeps its own directory.\n'
        "& {\n"
        "    $targetDir = '" + escaped + "'\n"
        "    $fresh = @($HOME)\n"
        "    if ($env:SystemRoot) {\n"
        "        $fresh += (Join-Path $env:SystemRoot 'System32')\n"
        "        $fresh += (Join-Path $env:SystemRoot 'Sysnative')\n"
        "    }\n"
        "    $fresh = $fresh | Where-Object { $_ } | ForEach-Object { $_.TrimEnd('\\') }\n"
        "    $here = (Get-Location).Path.TrimEnd('\\')\n"
        "    if (($fresh -contains $here) -and (Test-Path -LiteralPath $targetDir)) {\n"
        "        Set-Location -LiteralPath $targetDir\n"
        "    }\n"
        "}"
    )


# --- 4. Upsert a managed block into a profile ---------------------------------

class ProfileReadError(RuntimeError):
    """Raised when a PowerShell profile cannot be decoded or read."""


class ProfileDiscoveryError(RuntimeError):
    """Raised when an installed PowerShell edition's profile cannot be resolved."""


class ProfileMarkerError(ProfileReadError):
    """Raised when a managed profile marker pair is malformed."""


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
    """Read a profile and return ``(text, encoding)`` while preserving its BOM."""
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


def read_text(path):
    """Return profile text using the same decoding rules as ``read_profile``."""
    profile = read_profile(path)
    return profile[0] if profile is not None else None


def write_profile(path, content, encoding):
    """Write profile text without changing its existing line endings."""
    bom = PROFILE_BOMS.get(encoding, b"")
    codec = "utf-8" if encoding == "utf-8-sig" else encoding
    path.write_bytes(bom + content.encode(codec))


def _validate_profile_markers(content, begin, end, path):
    """Return the marker regex if the managed marker pair is well-formed."""
    events = sorted(
        [(match.start(), "begin") for match in re.finditer(re.escape(begin), content)] +
        [(match.start(), "end") for match in re.finditer(re.escape(end), content)]
    )
    depth = 0
    pairs = 0
    for _position, kind in events:
        if kind == "begin":
            if depth:
                raise ProfileMarkerError(
                    f"Malformed '{begin}' marker pair in {path}: nested begin markers "
                    "are not supported. No changes were written."
                )
            depth = 1
        elif not depth:
            raise ProfileMarkerError(
                f"Malformed '{end}' marker pair in {path}: end marker has no matching "
                "begin marker. No changes were written."
            )
        else:
            depth = 0
            pairs += 1

    if depth:
        raise ProfileMarkerError(
            f"Malformed '{begin}' marker pair in {path}: begin marker has no matching "
            "end marker. No changes were written."
        )
    if pairs > 1:
        raise ProfileMarkerError(
            f"Malformed '{begin}' marker pair in {path}: multiple managed blocks are "
            "not supported. No changes were written."
        )
    return re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)


def preflight_profile_markers(paths):
    """Validate every managed marker pair before any profile is written."""
    seen = set()
    for raw_path in paths:
        path = Path(raw_path)
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        if path.exists() and not path.is_file():
            raise ProfileReadError(
                f"PowerShell profile path '{path}' exists but is not a file. "
                "No changes were written."
            )
        profile = read_profile(path)
        if profile is None:
            continue
        content, _encoding = profile
        names = {
            match.group(1)
            for pattern in (MANAGED_BEGIN_RE, MANAGED_END_RE)
            for match in pattern.finditer(content)
        }
        events = [
            (match.start(), "begin", match.group(1))
            for match in MANAGED_BEGIN_RE.finditer(content)
        ] + [
            (match.start(), "end", match.group(1))
            for match in MANAGED_END_RE.finditer(content)
        ]
        active_name = None
        for _position, kind, name in sorted(events):
            if kind == "begin":
                if active_name is not None:
                    raise ProfileMarkerError(
                        f"Malformed powershell-setup markers in {path}: nested "
                        "managed blocks are not supported. No changes were written."
                    )
                active_name = name
            elif active_name != name:
                raise ProfileMarkerError(
                    f"Malformed powershell-setup markers in {path}: end marker "
                    "does not match its active begin marker. No changes were written."
                )
            else:
                active_name = None
        if active_name is not None:
            raise ProfileMarkerError(
                f"Malformed powershell-setup markers in {path}: begin marker has "
                "no matching end marker. No changes were written."
            )
        for name in sorted(names):
            _validate_profile_markers(
                content,
                MARKER_BEGIN.format(name=name),
                MARKER_END.format(name=name),
                path,
            )


def upsert_ps_block(path, name, body, conflict_pattern=None, conflict_label=None,
                    scan_paths=()):
    """Create or update the `name` marker block in `path` with `body` as content.

    If the marker is already present, its content is replaced in place. Otherwise,
    when `conflict_pattern` matches outside any of our markers - in `path` or in a
    scanned sibling profile - the block is skipped so an existing (possibly older)
    definition is never duplicated. New blocks are appended at the end of the file.
    """
    begin = MARKER_BEGIN.format(name=name)
    end = MARKER_END.format(name=name)
    new_block = f"{begin}\n{body}\n{end}"

    profile = read_profile(path)
    if profile is None:
        content, encoding = "", NEW_PROFILE_ENCODING
    else:
        content, encoding = profile

    marker_re = _validate_profile_markers(content, begin, end, path)
    if marker_re.search(content):
        updated = marker_re.sub(lambda _m: new_block, content, count=1)
        if updated != content:
            write_profile(path, updated, encoding)
            print(f"Updated the '{name}' block in {path}.")
        else:
            print(f"'{name}' block already up to date in {path}.")
        return

    if conflict_pattern:
        for candidate in [path, *scan_paths]:
            candidate_profile = read_profile(candidate)
            if candidate_profile is None:
                continue
            existing, _candidate_encoding = candidate_profile
            candidate_marker_re = _validate_profile_markers(
                existing, begin, end, candidate
            )
            if not existing or candidate_marker_re.search(existing):
                continue
            if re.search(conflict_pattern, existing, re.MULTILINE):
                label = conflict_label or name
                print(f"{label} already defined in {candidate} - leaving {path} untouched.")
                return

    path.parent.mkdir(parents=True, exist_ok=True)
    if content and not content.endswith("\n\n"):
        content = content.rstrip("\n") + "\n\n"
    write_profile(path, content + new_block + "\n", encoding)
    print(f"Installed the '{name}' block into {path}.")


# --- 5. Ask for the start directory -------------------------------------------

def prompt_start_dir():
    step("Choose the default PowerShell start directory")
    print(
        "PowerShell will Set-Location here when it starts from a 'fresh' launch\n"
        "(home or System32) - which is what a new WezTerm PowerShell tab does.\n"
        f"Press Enter to accept the default ({DEFAULT_START_DIR}).\n"
    )
    while True:
        raw = ask(f"Start directory [{DEFAULT_START_DIR}]: ").strip().strip('"')
        expanded = os.path.expandvars(os.path.expanduser(raw or DEFAULT_START_DIR))
        if Path(expanded).is_dir():
            return expanded
        print(f"'{expanded}' is not an existing directory - please enter a valid path.")


# --- 6. Apply everything ------------------------------------------------------

def apply_start_directory(targets, start_dir):
    step(f"Wiring the default start directory: {start_dir}")
    body = start_directory_body(start_dir)
    for target in targets:
        upsert_ps_block(target, "start-directory", body)


# --- main ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-dir", default=None,
                        help="Default start directory to wire in non-interactively; must be "
                             "an existing directory or it's skipped. If omitted (and "
                             "--no-start-dir / --skip-config aren't given), you'll be prompted "
                             f"(default {DEFAULT_START_DIR}). ~ and %%ENV%% are expanded.")
    parser.add_argument("--no-start-dir", dest="start_directory", action="store_false",
                        default=True, help="Don't configure a start directory.")
    parser.add_argument("--skip-install", action="store_true",
                        help="Don't install pwsh - only edit the profile.")
    parser.add_argument("--skip-config", action="store_true",
                        help="Don't touch any profile - just install pwsh.")
    args = parser.parse_args()

    pwsh_executable = find_pwsh_executable()
    if not args.skip_install:
        if not install_pwsh():
            return 1
        pwsh_executable = find_pwsh_executable()
        if not pwsh_executable:
            print("PowerShell 7 was installed but could not be located for profile "
                  "configuration.", file=sys.stderr)
            return 1

    if args.skip_config:
        step("Done. Open a new PowerShell session for changes to take effect.")
        return 0

    targets, scan_paths = resolve_profile_paths(pwsh_executable=pwsh_executable)
    if not targets:
        print("\nNo PowerShell profile could be resolved (is pwsh/powershell installed?) - "
              "skipping profile configuration.")
        return 0
    scan_paths = _unique_paths([*scan_paths, *sibling_scan_paths(targets)])
    preflight_profile_markers([*targets, *scan_paths])
    print("\nProfiles to configure:")
    for target in targets:
        print(f"  - {target}")

    if args.start_directory:
        start_dir = args.start_dir
        if start_dir is None:
            start_dir = prompt_start_dir()
        else:
            start_dir = os.path.expandvars(os.path.expanduser(start_dir))
            if not Path(start_dir).is_dir():
                print(f"--start-dir '{start_dir}' is not an existing directory - "
                      "skipping start-directory configuration.")
                start_dir = None
        if start_dir:
            apply_start_directory(targets, start_dir)

    print("\nFor the personal shell commands (ai / ll / np / ...), run "
          "scripts\\setup-shell-extras.ps1.")
    step("Done. Open a new PowerShell session for changes to take effect.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
    except (ProfileReadError, ProfileDiscoveryError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
