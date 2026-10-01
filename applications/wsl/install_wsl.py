#!/usr/bin/env python3
"""
Sets up a WSL2 development instance on Windows:
  - makes sure WSL2 is enabled and a base distro (Ubuntu-24.04 by default) is
    installed, then renames the registered instance to "personal" (export + import) so
    that name stays stable even if the underlying Ubuntu release changes later;
    existing WSL1 instances are converted to WSL2 before continuing
  - offers to set a custom hostname (default "personal-wsl") in /etc/wsl.conf, since
    WSL2 otherwise mirrors the Windows machine's hostname
  - offers to copy an existing Windows working directory into the instance's native
    filesystem (cross-OS I/O is slow, so files you build/run from WSL should
    actually live there, not just be reachable via /mnt/<drive>). Uses rsync
    (installed on demand if missing) for a live progress display; falls back to a
    plain `cp` if rsync can't be installed.
  - offers to install yazi inside the instance with the same setup as
    ../yazi/install_yazi.py (adds configurable drive and working-directory shortcuts)
  - offers to install JetBrains Gateway and prints the steps to open repos with a
    native-speed IDE backend running inside WSL

This is the WSL half of the setup; `../wezterm/install_wezterm.py` installs WezTerm
and (for the "wsl" approach) points it at the instance this script creates.

Safe to re-run: every step only adds/updates what is missing and asks before doing
anything destructive.
"""

import argparse
import codecs
from contextlib import contextmanager
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

IS_WINDOWS = platform.system() == "Windows"

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WSL_DISTRO = os.environ.get("WORKSPACE_PERSONAL_WSL_DISTRO", "Ubuntu-24.04")
DEFAULT_WSL_INSTANCE_NAME = os.environ.get("WORKSPACE_PERSONAL_WSL_INSTANCE", "personal")
DEFAULT_WSL_HOSTNAME = os.environ.get("WORKSPACE_PERSONAL_WSL_HOSTNAME", "personal-wsl")
DEFAULT_WINDOWS_SOURCE_DIR = os.environ.get(
    "WORKSPACE_PERSONAL_WINDOWS_DIR", str(WORKSPACE_ROOT))
DEFAULT_WSL_WORKING_TARGET = os.environ.get(
    "WORKSPACE_PERSONAL_WSL_WORKING_TARGET",
    os.environ.get("WORKSPACE_PERSONAL_WSL_DEV_TARGET", "~/workspace"),
)
DEFAULT_WSL_YAZI_TARGET = os.environ.get("WORKSPACE_PERSONAL_WSL_YAZI_TARGET")

WSL_CONF_MARKER_BEGIN = "# >>> wsl-setup: {name} >>>"
WSL_CONF_MARKER_END = "# <<< wsl-setup: {name} <<<"
WSL_MANAGED_BEGIN_RE = re.compile(
    re.escape("# >>> wsl-setup: ") + r"(?P<name>.*?)" + re.escape(" >>>")
)
WSL_MANAGED_END_RE = re.compile(
    re.escape("# <<< wsl-setup: ") + r"(?P<name>.*?)" + re.escape(" <<<")
)


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


class ManagedMarkerError(ValueError):
    """Raised when a managed WSL configuration marker namespace is malformed."""


class WslConfReadError(RuntimeError):
    """Raised when /etc/wsl.conf cannot be read safely."""


_INVALID_WSL_NAME_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def validate_wsl_name(value, label="WSL name"):
    """Validate a WSL distro/instance name before it reaches a filesystem path."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must not be empty.")
    if value != value.strip() or value in (".", ".."):
        raise ValueError(f"{label} '{value}' is not a supported name.")
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"{label} '{value}' must not be a rooted path.")
    if _INVALID_WSL_NAME_CHARS.search(value):
        raise ValueError(
            f"{label} '{value}' contains unsupported path characters."
        )
    if value.endswith((".", " ")):
        raise ValueError(f"{label} '{value}' must not end with a dot or space.")
    if value.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"{label} '{value}' is a reserved Windows device name.")
    return value


def _path_beneath_root(root, child_name, label):
    """Resolve a generated child path and prove it remains under its root."""
    root_path = Path(root).expanduser().resolve()
    child_path = (root_path / child_name).resolve()
    try:
        child_path.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(
            f"{label} '{child_path}' escapes configured root '{root_path}'."
        ) from exc
    return child_path


def _encoded_wsl_name(instance_name):
    validate_wsl_name(instance_name, "WSL instance name")
    return quote(
        instance_name,
        safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-",
    )


def validate_managed_markers(content, path):
    """Refuse duplicate, nested, unmatched, or out-of-order managed blocks."""
    events = [
        (match.start(), "begin", match.group("name"))
        for match in WSL_MANAGED_BEGIN_RE.finditer(content)
    ] + [
        (match.start(), "end", match.group("name"))
        for match in WSL_MANAGED_END_RE.finditer(content)
    ]
    active_name = None
    completed = set()
    for _position, kind, name in sorted(events):
        if not name.strip():
            raise ManagedMarkerError(
                f"Malformed WSL markers in {path}: marker name is empty. "
                "No changes were written."
            )
        if kind == "begin":
            if active_name is not None:
                raise ManagedMarkerError(
                    f"Malformed WSL markers in {path}: nested managed blocks are "
                    "not supported. No changes were written."
                )
            if name in completed:
                raise ManagedMarkerError(
                    f"Malformed WSL markers in {path}: managed block '{name}' "
                    "appears more than once. No changes were written."
                )
            active_name = name
        elif active_name is None:
            raise ManagedMarkerError(
                f"Malformed WSL markers in {path}: end marker '{name}' has no "
                "matching begin marker. No changes were written."
            )
        elif active_name != name:
            raise ManagedMarkerError(
                f"Malformed WSL markers in {path}: end marker '{name}' does not "
                f"match active block '{active_name}'. No changes were written."
            )
        else:
            completed.add(active_name)
            active_name = None

    if active_name is not None:
        raise ManagedMarkerError(
            f"Malformed WSL markers in {path}: begin marker '{active_name}' has "
            "no matching end marker. No changes were written."
        )


def resolve_effective_wsl_user(instance_name):
    """Return the user selected by a distro's current default login."""
    validate_wsl_name(instance_name, "WSL instance name")
    try:
        result = run(
            ["wsl", "-d", instance_name, "--", "id", "-un"],
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Could not determine the effective Linux user for '{instance_name}': {exc}")
        return None

    if result.returncode != 0:
        print(f"Could not determine the effective Linux user for '{instance_name}' "
              f"(exit code {result.returncode}).")
        return None

    username = decode_wsl_output(result.stdout or b"").strip()
    if (not username or any(char in username for char in "\r\n")
            or any(char.isspace() for char in username)
            or any(char in username for char in "=;#[]")):
        print(f"WSL returned an invalid default Linux username for '{instance_name}'.")
        return None
    return username


def resolve_effective_wsl_uid(instance_name):
    """Return the numeric UID selected by a distro's current default login."""
    validate_wsl_name(instance_name, "WSL instance name")
    try:
        result = run(
            ["wsl", "-d", instance_name, "--", "id", "-u"],
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Could not determine the effective Linux UID for '{instance_name}': {exc}")
        return None

    if result.returncode != 0:
        print(f"Could not determine the effective Linux UID for '{instance_name}' "
              f"(exit code {result.returncode}).")
        return None

    raw_uid = decode_wsl_output(result.stdout or b"").strip()
    if not re.fullmatch(r"[0-9]+", raw_uid):
        print(f"WSL returned an invalid effective Linux UID for '{instance_name}'.")
        return None
    return int(raw_uid)


def resolve_verified_non_root_wsl_user(instance_name):
    """Return a verified non-root default user for source-user capture."""
    validate_wsl_name(instance_name, "WSL instance name")
    username = resolve_effective_wsl_user(instance_name)
    if not username:
        return None
    uid = resolve_effective_wsl_uid(instance_name)
    if uid is None:
        return None
    if uid == 0:
        print(f"'{instance_name}' currently defaults to UID 0 (reported username "
              f"'{username}'). Complete the distro's first-launch Linux username "
              "setup, then re-run this installer.")
        return None
    return username


def verify_effective_wsl_user(instance_name, expected_user=None):
    """Refuse setup until the distro opens as a usable, non-root user."""
    validate_wsl_name(instance_name, "WSL instance name")
    username = resolve_effective_wsl_user(instance_name)
    if not username:
        print(f"Refusing setup for '{instance_name}': the effective Linux user "
              "could not be verified. Run `wsl -d "
              f"{instance_name} -- id -un` and fix the distro before re-running.")
        return False
    uid = resolve_effective_wsl_uid(instance_name)
    if uid is None:
        print(f"Refusing setup for '{instance_name}': the effective Linux UID "
              "could not be verified.")
        return False
    if uid == 0:
        print(f"Refusing setup for '{instance_name}': it opens as root (UID 0), "
              f"reported as '{username}'. Configure a non-root default user in the "
              "distro (for example, [user] default = <username> in /etc/wsl.conf), "
              "terminate WSL, and re-run.")
        return False
    if expected_user is not None and username != expected_user:
        print(f"Refusing setup for '{instance_name}': expected default user "
              f"'{expected_user}', got '{username}'. Repair /etc/wsl.conf, terminate "
              "the distro, and re-run.")
        return False
    return True


def install_package(display_name, winget_id=None, manual_hint=None):
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

    print(f"Could not find a supported package manager for {display_name}.")
    if manual_hint:
        print(manual_hint)
    print("The requested installation was not completed.")
    return False


# --- 1. WSL2 base distro + rename to a stable instance name ("personal") ----

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


def list_wsl_distro_info():
    """Return installed distro names, states, and WSL versions, or None if the
    WSL query itself failed."""
    try:
        result = subprocess.run(
            ["wsl", "--list", "--verbose"], capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    distros = []
    for raw_line in decode_wsl_output(result.stdout).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("*"):
            line = line[1:].strip()

        columns = line.split()
        if len(columns) < 3 or columns[-1] not in ("1", "2"):
            continue

        name = " ".join(columns[:-2])
        if name:
            distros.append({
                "name": name,
                "state": columns[-2],
                "version": int(columns[-1]),
            })
    return distros


def list_wsl_distros():
    """Return the installed distro NAMEs (as `wsl` identifies them), or [] if
    WSL itself isn't installed yet."""
    return [distro["name"] for distro in (list_wsl_distro_info() or [])]


def find_wsl_distro(instance_name):
    validate_wsl_name(instance_name, "WSL instance name")
    for distro in list_wsl_distro_info() or []:
        if distro["name"].lower() == instance_name.lower():
            return distro
    return None


def is_distro_installed(instance_name):
    return find_wsl_distro(instance_name) is not None


def ensure_wsl2(instance_name, expected_user=None):
    """Return True only when `instance_name` is registered as a WSL2 distro."""
    distro = find_wsl_distro(instance_name)
    if not distro:
        return False
    if distro["version"] == 2:
        return verify_effective_wsl_user(instance_name, expected_user=expected_user)

    print(f"'{instance_name}' is registered as WSL1; converting it to WSL2.")
    result = run(["wsl", "--set-version", instance_name, "2"])
    if result.returncode != 0:
        print(f"Could not convert '{instance_name}' to WSL2 (exit code "
              f"{result.returncode}).")
        return False

    converted = find_wsl_distro(instance_name)
    if converted and converted["version"] == 2:
        if not verify_effective_wsl_user(instance_name, expected_user=expected_user):
            return False
        print(f"'{instance_name}' is now a WSL2 instance.")
        return True

    print(f"'{instance_name}' did not report WSL2 after conversion; refusing to continue.")
    return False


def default_export_path(base_distro, instance_name):
    validate_wsl_name(base_distro, "Base WSL distro name")
    validate_wsl_name(instance_name, "WSL instance name")
    export_root = Path(
        os.environ.get("WORKSPACE_PERSONAL_WSL_EXPORT_DIR")
        or (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "workspace-personal" / "wsl-exports")
    )
    return _path_beneath_root(
        export_root,
        f"{base_distro}-to-{instance_name}.tar",
        "WSL export archive",
    )


def _wsl_target_lock_path(instance_name):
    encoded_name = _encoded_wsl_name(instance_name)
    lock_root = Path(
        os.environ.get("WORKSPACE_PERSONAL_WSL_LOCK_DIR")
        or (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "workspace-personal" / "wsl-locks")
    )
    return _path_beneath_root(
        lock_root, f"{encoded_name}.lock", "WSL target lock file"
    )


@contextmanager
def wsl_target_import_lock(instance_name):
    """Serialize target checks/imports for one WSL instance name."""
    lock_path = _wsl_target_lock_path(instance_name)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            locked = False
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                locked = True
                yield
            finally:
                if locked:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _expected_wsl_user_path(instance_name):
    encoded_name = _encoded_wsl_name(instance_name)
    state_root = Path(
        os.environ.get("WORKSPACE_PERSONAL_WSL_USER_STATE_DIR")
        or (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "workspace-personal" / "wsl-expected-users")
    )
    return _path_beneath_root(
        state_root, f"{encoded_name}.txt", "expected WSL user state file"
    )


def persist_expected_wsl_user(instance_name, username):
    """Persist the source user's expected target default for future reruns."""
    path = _expected_wsl_user_path(instance_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(username + "\n", encoding="utf-8")


def load_expected_wsl_user(instance_name):
    """Return a persisted expected source user, or None when no state exists."""
    path = _expected_wsl_user_path(instance_name)
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(
            f"Expected WSL user state '{path}' exists but is not a file."
        )
    username = path.read_text(encoding="utf-8").strip()
    if (not username or any(char in username for char in "\r\n")
            or any(char.isspace() for char in username)
            or any(char in username for char in "=;#[]")):
        raise ValueError(f"Expected WSL user state '{path}' is invalid.")
    return username


def resolve_existing_target_expected_user(base_distro, instance_name, installed):
    """Resolve and persist the expected user for an existing target distro."""
    try:
        expected_user = load_expected_wsl_user(instance_name)
    except (OSError, ValueError) as exc:
        print(f"Could not read expected default user for '{instance_name}': {exc}")
        return False, None

    if expected_user is None and any(
        distro["name"].lower() == base_distro.lower()
        for distro in installed
    ):
        expected_user = resolve_verified_non_root_wsl_user(base_distro)
        if not expected_user:
            return False, None
        try:
            persist_expected_wsl_user(instance_name, expected_user)
        except OSError as exc:
            print(f"Could not persist expected default user for '{instance_name}': {exc}")
            return False, None

    return True, expected_user


def cleanup_imported_distro(instance_name, registration_owned):
    """Remove an imported target only when this process proved ownership."""
    validate_wsl_name(instance_name, "WSL instance name")
    if not registration_owned:
        print(f"Refusing to unregister '{instance_name}' because this process did "
              "not prove ownership of its registration.")
        return False
    try:
        result = run(["wsl", "--unregister", instance_name])
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Could not remove partial WSL target '{instance_name}': {exc}")
        print(f"Recovery: inspect `wsl --list --verbose`, repair the default user "
              f"for '{instance_name}', or run `wsl --unregister {instance_name}` "
              "manually before retrying.")
        return False
    if result.returncode == 0:
        print(f"Removed partial WSL target '{instance_name}'.")
        return True
    print(f"Could not remove partial WSL target '{instance_name}' (exit code "
          f"{result.returncode}).")
    print(f"Recovery: inspect `wsl --list --verbose`, repair the default user for "
          f"'{instance_name}', or run `wsl --unregister {instance_name}` manually "
          "before retrying.")
    return False


def repair_imported_default_user(instance_name, username):
    """Retry a safe default-user repair once before abandoning an import."""
    for attempt in range(2):
        if establish_imported_default_user(instance_name, username):
            return True
        if attempt == 0:
            print(f"Retrying default-user repair for '{instance_name}'.")
    return False


def rename_distro(base_distro, instance_name, export_path=None):
    """Export `base_distro` and re-import it as `instance_name`, then offer to
    unregister the original. The effective non-root Linux user is captured
    before export and explicitly restored in the imported distro."""
    validate_wsl_name(base_distro, "Base WSL distro name")
    validate_wsl_name(instance_name, "WSL instance name")
    source_user = resolve_verified_non_root_wsl_user(base_distro)
    if not source_user:
        return False

    with wsl_target_import_lock(instance_name):
        return _rename_distro_locked(
            base_distro, instance_name, export_path, source_user,
        )


def _rename_distro_locked(base_distro, instance_name, export_path, source_user):
    """Perform a target import while holding the per-instance import lock."""
    validate_wsl_name(base_distro, "Base WSL distro name")
    validate_wsl_name(instance_name, "WSL instance name")

    installed = list_wsl_distro_info()
    if installed is None:
        print(f"Could not verify that target WSL distro '{instance_name}' is absent; "
              "refusing to import.")
        return False
    target_preexisting = any(
        distro["name"].lower() == instance_name.lower() for distro in installed
    )
    if target_preexisting:
        print(f"'{instance_name}' is already registered; refusing to import over it.")
        return False

    install_root = _path_beneath_root(
        Path(os.environ["LOCALAPPDATA"]) / "WSL",
        instance_name,
        "WSL import directory",
    )
    install_root.mkdir(parents=True, exist_ok=True)

    tar_path = Path(export_path).expanduser() if export_path else default_export_path(
        base_distro, instance_name)
    if tar_path.exists():
        answer = ask(f"Export archive '{tar_path}' already exists. Replace it? [y/N] ")
        if not answer.strip().lower().startswith("y"):
            print("Skipping rename so the existing export archive is not overwritten.")
            return None
        tar_path.unlink()
    tar_path.parent.mkdir(parents=True, exist_ok=True)

    step(f"Exporting '{base_distro}' (this can take a few minutes)")
    run(["wsl", "--export", base_distro, str(tar_path)], check=True)
    try:
        persist_expected_wsl_user(instance_name, source_user)
    except OSError as exc:
        print(f"Could not persist expected default user for '{instance_name}': {exc}")
        print(f"Export archive retained at '{tar_path}' for troubleshooting.")
        return False
    try:
        step(f"Importing as '{instance_name}'")
        run(["wsl", "--import", instance_name, str(install_root), str(tar_path),
             "--version", "2"], check=True)
    except subprocess.CalledProcessError:
        cleanup_imported_distro(instance_name, registration_owned=False)
        print(f"Export archive retained at '{tar_path}' for troubleshooting.")
        raise

    if not repair_imported_default_user(instance_name, source_user):
        removed = cleanup_imported_distro(instance_name, registration_owned=True)
        print(f"Could not establish and verify default user '{source_user}' for "
              f"'{instance_name}'.")
        if not removed:
            print(f"Do not continue setup as root. Repair the target manually before "
                  f"retrying; the export archive is retained at '{tar_path}'.")
        else:
            print(f"Export archive retained at '{tar_path}' for troubleshooting.")
        return False

    cleanup_failed = False
    try:
        tar_path.unlink()
    except OSError as exc:
        print(f"Imported '{instance_name}', but could not remove export archive "
              f"'{tar_path}': {exc}")
        cleanup_failed = True

    print(f"'{instance_name}' created from '{base_distro}'.")

    answer = ask(f"Unregister the original '{base_distro}' entry now that '{instance_name}' "
                 f"exists? [y/N] ")
    if answer.strip().lower().startswith("y"):
        result = run(["wsl", "--unregister", base_distro])
        if result.returncode == 0:
            print(f"Unregistered '{base_distro}'.")
        else:
            print(f"Could not unregister '{base_distro}' (exit code "
                  f"{result.returncode}).")
            cleanup_failed = True
    else:
        print(f"Left '{base_distro}' registered alongside '{instance_name}'.")
    if cleanup_failed:
        print("WSL rename completed with cleanup failures; the archive or original "
              "distro remains and must be handled before retrying.")
        return False
    return True


def configure_wsl(base_distro, instance_name, export_path=None):
    """Return True when ready, False on failure, or None when setup is deferred."""
    validate_wsl_name(base_distro, "Base WSL distro name")
    validate_wsl_name(instance_name, "WSL instance name")
    step(f"Checking WSL2 instance '{instance_name}' (based on {base_distro})")

    if not IS_WINDOWS:
        print("WSL is Windows-only - skipping.")
        return False

    if not shutil.which("wsl"):
        print("`wsl` command not found. Enable it first (needs admin + a reboot):")
        print("  wsl --install")
        print("Re-run this script afterwards to install the distro.")
        return False

    installed = list_wsl_distro_info()
    if installed is None:
        print("Could not query the installed WSL distributions.")
        return False

    existing = next(
        (distro for distro in installed
         if distro["name"].lower() == instance_name.lower()),
        None,
    )
    if existing:
        if existing["version"] == 2:
            print(f"'{instance_name}' is already installed as WSL2.")
        resolved, expected_user = resolve_existing_target_expected_user(
            base_distro, instance_name, installed,
        )
        if not resolved:
            return False
        return ensure_wsl2(instance_name, expected_user=expected_user)

    if not any(distro["name"].lower() == base_distro.lower() for distro in installed):
        installed_names = [distro["name"] for distro in installed]
        print(f"'{base_distro}' is not installed. Installed distros: "
              f"{installed_names or '(none)'}")
        answer = ask(f"Install '{base_distro}' now via `wsl --install -d {base_distro} "
                     f"--no-launch`? [y/N] ")
        if not answer.strip().lower().startswith("y"):
            print("Skipping WSL distro install.")
            return None
        result = run(["wsl", "--install", "-d", base_distro, "--no-launch"])
        if result.returncode != 0:
            print(f"WSL could not install '{base_distro}' (exit code "
                  f"{result.returncode}). Re-run after resolving the error above.")
            return False
        print(f"'{base_distro}' installed. Launch it once (`wsl -d {base_distro}`) to finish "
              f"first-time setup (create a Linux username/password), then re-run this script "
              f"to rename it to '{instance_name}'.")
        return None

    ready = ask(f"'{base_distro}' is registered. Have you already launched it once and "
                f"completed the Linux username/password setup? [y/N] ")
    if not ready.strip().lower().startswith("y"):
        print(f"Run `wsl -d {base_distro}` once to finish setup, then re-run this script "
              f"to rename it to '{instance_name}'.")
        return None

    answer = ask(f"Rename '{base_distro}' to '{instance_name}' now (export + import)? [y/N] ")
    if not answer.strip().lower().startswith("y"):
        print("Skipping rename.")
        return None

    renamed = rename_distro(base_distro, instance_name, export_path)
    if renamed is None:
        return None
    if not renamed:
        return False
    return ensure_wsl2(instance_name)


def ensure_existing_target_wsl2(base_distro, instance_name):
    """Validate an existing target and enforce its persisted source user."""
    validate_wsl_name(base_distro, "Base WSL distro name")
    validate_wsl_name(instance_name, "WSL instance name")
    installed = list_wsl_distro_info()
    if installed is None:
        print("Could not query the installed WSL distributions.")
        return False
    if not any(
        distro["name"].lower() == instance_name.lower()
        for distro in installed
    ):
        print(f"'{instance_name}' is not installed.")
        return False

    resolved, expected_user = resolve_existing_target_expected_user(
        base_distro, instance_name, installed,
    )
    if not resolved:
        return False
    return ensure_wsl2(instance_name, expected_user=expected_user)


# --- 2. Custom hostname (WSL syncs the Windows machine name by default) -----

def _confirm_wsl_conf_missing(instance_name):
    try:
        result = subprocess.run(
            ["wsl", "-d", instance_name, "-u", "root", "--",
             "test", "!", "-e", "/etc/wsl.conf"],
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WslConfReadError(
            f"Could not confirm whether /etc/wsl.conf is absent in "
            f"'{instance_name}': {exc}"
        ) from exc
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise WslConfReadError(
        f"Could not confirm whether /etc/wsl.conf is absent in "
        f"'{instance_name}' (exit code {result.returncode})."
    )


def read_wsl_conf(instance_name):
    validate_wsl_name(instance_name, "WSL instance name")
    try:
        result = subprocess.run(
            ["wsl", "-d", instance_name, "-u", "root", "--",
             "cat", "/etc/wsl.conf"],
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WslConfReadError(
            f"Could not read /etc/wsl.conf in '{instance_name}': {exc}"
        ) from exc

    if result.returncode == 0:
        raw = result.stdout or b""
        try:
            return raw if isinstance(raw, str) else raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise WslConfReadError(
                f"/etc/wsl.conf in '{instance_name}' is not valid UTF-8."
            ) from exc

    if _confirm_wsl_conf_missing(instance_name):
        return ""

    stderr = (result.stderr or b"")
    if not isinstance(stderr, str):
        stderr = stderr.decode("utf-8", errors="replace")
    detail = stderr.strip()
    suffix = f": {detail}" if detail else ""
    raise WslConfReadError(
        f"Could not read /etc/wsl.conf in '{instance_name}' "
        f"(exit code {result.returncode}){suffix}"
    )


def write_wsl_conf(instance_name, content):
    validate_wsl_name(instance_name, "WSL instance name")
    subprocess.run(
        ["wsl", "-d", instance_name, "-u", "root", "--", "tee", "/etc/wsl.conf"],
        input=content.encode("utf-8"), capture_output=True, check=True,
    )


def _wsl_section_ranges(content):
    section_re = re.compile(
        r"(?im)^[ \t]*\[\s*([^\]\r\n]+?)\s*\][ \t]*"
        r"(?:[;#][^\r\n]*)?(?:\r\n|\n|\r|$)"
    )
    matches = list(section_re.finditer(content))
    sections = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        sections.append(
            {
                "name": match.group(1).strip().lower(),
                "start": match.start(),
                "body_start": match.end(),
                "end": end,
            }
        )
    return sections


def _wsl_section_keys(content, section):
    keys = {}
    for line in content[section["body_start"]:section["end"]].splitlines():
        match = re.match(r"^\s*([^#;=\s]+)\s*=\s*(.*?)\s*(?:[;#].*)?$", line)
        if match:
            keys.setdefault(match.group(1).lower(), []).append(match.group(2).strip())
    return keys


def _wsl_newline(content):
    if "\r\n" in content:
        return "\r\n"
    if "\r" in content:
        return "\r"
    return "\n"


def merge_wsl_default_user(content, username):
    """Merge a default user into [user] without rewriting other settings."""
    newline = _wsl_newline(content)
    user_sections = [
        section for section in _wsl_section_ranges(content) if section["name"] == "user"
    ]
    if user_sections:
        section = user_sections[0]
        section_content = content[section["body_start"]:section["end"]]
        default_re = re.compile(
            r"(?im)^([ \t]*default[ \t]*=[ \t]*)([^#;\r\n]*?)"
            r"([ \t]*(?:[;#][^\r\n]*)?)(\r\n|\n|\r|$)"
        )
        match = default_re.search(section_content)
        if match:
            current = match.group(2).strip().strip("'\"")
            if current == username:
                return content
            replacement = (
                match.group(1) + username + match.group(3) + match.group(4)
            )
            section_content = (
                section_content[:match.start()]
                + replacement
                + section_content[match.end():]
            )
        else:
            if section_content and not section_content.endswith(("\r", "\n")):
                section_content += newline
            section_content += f"default = {username}{newline}"
        return (
            content[:section["body_start"]]
            + section_content
            + content[section["end"]:]
        )

    user_block = f"[user]{newline}default = {username}{newline}"
    if not content:
        return user_block
    if content.endswith(newline):
        separator = "" if content.endswith(newline * 2) else newline
    else:
        separator = newline * 2
    return content + separator + user_block


def _render_hostname_block(begin, end, hostname, newline, include_section,
                           include_generate_hosts=True):
    lines = [begin]
    if include_section:
        lines.append("[network]")
    lines.append(f"hostname = {hostname}")
    if include_generate_hosts:
        lines.append("generateHosts = false")
    lines.append(end)
    return newline.join(lines)


def establish_imported_default_user(instance_name, username):
    """Persist and verify the imported distro's non-root default user."""
    try:
        original_content = read_wsl_conf(instance_name)
        new_content = merge_wsl_default_user(original_content, username)
        if new_content != original_content:
            write_wsl_conf(instance_name, new_content)
        result = run(["wsl", "--terminate", instance_name])
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Could not configure the default user for '{instance_name}': {exc}")
        return False

    if result.returncode != 0:
        print(f"Could not terminate '{instance_name}' after setting its default user "
              f"(exit code {result.returncode}).")
        return False

    verified_user = resolve_effective_wsl_user(instance_name)
    verified_uid = resolve_effective_wsl_uid(instance_name)
    if verified_user != username or verified_uid is None or verified_uid == 0:
        verified_uid_text = (
            str(verified_uid) if verified_uid is not None else "unknown"
        )
        print(
            f"Default user verification failed for '{instance_name}': expected "
            f"non-root user '{username}', got '{verified_user or '(none)'}' "
            f"(UID {verified_uid_text})."
        )
        return False
    print(f"Verified '{instance_name}' opens as Linux user '{username}'.")
    return True


def configure_wsl_hostname(instance_name, hostname=None):
    validate_wsl_name(instance_name, "WSL instance name")
    step(f"Hostname for '{instance_name}'")

    if hostname is None:
        answer = ask(f"Change the hostname inside '{instance_name}'? [y/N] ")
        if not answer.strip().lower().startswith("y"):
            return True
        entered = ask(f"Hostname to use inside '{instance_name}' [{DEFAULT_WSL_HOSTNAME}]: ")
        hostname = entered.strip() or DEFAULT_WSL_HOSTNAME

    name = "hostname"
    begin = WSL_CONF_MARKER_BEGIN.format(name=name)
    end = WSL_CONF_MARKER_END.format(name=name)
    original_content = read_wsl_conf(instance_name)
    validate_managed_markers(original_content, f"/etc/wsl.conf in '{instance_name}'")
    newline = _wsl_newline(original_content)
    marker_re = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)

    marker_match = marker_re.search(original_content)
    content_without_marker = (
        original_content[:marker_match.start()] + original_content[marker_match.end():]
        if marker_match else original_content
    )
    sections_without_marker = _wsl_section_ranges(content_without_marker)
    network_sections = [
        section for section in sections_without_marker if section["name"] == "network"
    ]

    manual_hostnames = []
    for section in network_sections:
        keys = _wsl_section_keys(content_without_marker, section)
        manual_hostnames.extend(keys.get("hostname", []))
    normalized_hostname = hostname.strip().strip("'\"").lower()
    for manual_hostname in manual_hostnames:
        if manual_hostname.strip().strip("'\"").lower() != normalized_hostname:
            print(f"/etc/wsl.conf inside '{instance_name}' already sets a hostname manually - "
                  "leaving it untouched. Edit it by hand if you want this script to manage it.")
            return True
    if manual_hostnames and marker_match is None:
        print(f"Hostname is already set to '{hostname}' in '{instance_name}' - nothing to do.")
        return True

    marker_has_section = bool(
        marker_match and any(
            section["name"] == "network"
            for section in _wsl_section_ranges(marker_match.group(0))
        )
    )
    marker_network_index = None
    if marker_match and not marker_has_section:
        network_index = 0
        for section in _wsl_section_ranges(original_content):
            if section["name"] != "network":
                continue
            if section["body_start"] <= marker_match.start() < section["end"]:
                marker_network_index = network_index
                break
            network_index += 1

    if marker_match and marker_has_section and not network_sections:
        new_block = _render_hostname_block(
            begin, end, hostname, newline, include_section=True,
        )
        new_content = marker_re.sub(lambda _m: new_block, original_content, count=1)
    elif marker_match and marker_has_section and network_sections:
        # An older run may have added a standalone [network] block even though
        # the file already had one. Drop only that managed block and merge its
        # settings into the existing section below.
        original_content = content_without_marker
        marker_match = None
        network_sections = [
            section for section in _wsl_section_ranges(original_content)
            if section["name"] == "network"
        ]

    if marker_match and not marker_has_section and marker_network_index is not None:
        section = network_sections[min(marker_network_index, len(network_sections) - 1)]
        section_keys = _wsl_section_keys(content_without_marker, section)
        include_generate_hosts = "generatehosts" not in section_keys
        new_block = _render_hostname_block(
            begin, end, hostname, newline, include_section=False,
            include_generate_hosts=include_generate_hosts,
        )
        # Use a callable replacement so backslashes/backrefs in new_block are
        # never interpreted by re.sub's string-replacement escaping rules.
        new_content = marker_re.sub(lambda _m: new_block, original_content, count=1)
    elif marker_match and not marker_has_section:
        # A key-only marker outside a [network] section is not safe to update
        # in place; rebuild it through the normal section-aware path.
        original_content = content_without_marker
        marker_match = None
        network_sections = [
            section for section in _wsl_section_ranges(original_content)
            if section["name"] == "network"
        ]

    if marker_match is None and network_sections:
        section = network_sections[0]
        section_keys = _wsl_section_keys(original_content, section)
        include_generate_hosts = "generatehosts" not in section_keys
        new_block = _render_hostname_block(
            begin, end, hostname, newline, include_section=False,
            include_generate_hosts=include_generate_hosts,
        )
        insert_at = section["end"]
        prefix = original_content[:insert_at]
        separator = "" if not prefix or prefix.endswith(("\n", "\r")) else newline
        new_content = prefix + separator + new_block + newline + original_content[insert_at:]
    elif marker_match is None:
        new_block = _render_hostname_block(
            begin, end, hostname, newline, include_section=True,
        )
        prefix = original_content.rstrip("\r\n")
        separator = newline * (2 if prefix.strip() else 0)
        new_content = prefix + separator + new_block + newline

    if new_content == original_content:
        print(f"Hostname is already set to '{hostname}' in '{instance_name}' - nothing to do.")
        return True

    write_wsl_conf(instance_name, new_content)
    print(f"Set hostname to '{hostname}' in '{instance_name}':/etc/wsl.conf.")

    step(f"Restarting '{instance_name}' for the hostname change to take effect")
    result = run(["wsl", "--terminate", instance_name])
    if result.returncode == 0:
        print(f"Reopen '{instance_name}' (e.g. from WezTerm) to see the new hostname.")
    else:
        print(f"Could not restart '{instance_name}' (exit code {result.returncode}); "
              "restart it manually for the hostname change to take effect.")
        return False
    return True


# --- 3. Copy an existing Windows working directory into the WSL instance ----

def windows_path_to_wsl(path):
    """Translate an absolute Windows path to its /mnt/<drive> form inside WSL."""
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if not drive:
        raise ValueError(f"'{path}' is not an absolute Windows path with a drive letter.")
    rest = str(resolved)[len(resolved.drive):].replace("\\", "/")
    return f"/mnt/{drive}{rest}"


def windows_drive_mount(path):
    """Return the WSL mount for a Windows drive (e.g. V:\\ -> /mnt/v)."""
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if not drive:
        raise ValueError(f"'{path}' is not an absolute Windows path with a drive letter.")
    return f"/mnt/{drive}"


def resolve_wsl_home(instance_name):
    validate_wsl_name(instance_name, "WSL instance name")
    result = subprocess.run(
        ["wsl", "-d", instance_name, "--", "bash", "-lc", "echo $HOME"],
        capture_output=True, text=True,
    )
    home = result.stdout.strip()
    return home or None


def expand_wsl_target(instance_name, target):
    """Resolve a leading '~' to the instance's actual $HOME before it gets
    shlex.quote()'d - single-quoting a '~/workspace' path would otherwise stop bash from
    expanding the tilde at all, so `cp`/`mkdir` see a literal '~' path."""
    validate_wsl_name(instance_name, "WSL instance name")
    if target != "~" and not target.startswith("~/"):
        return target
    home = resolve_wsl_home(instance_name)
    if not home:
        print(f"Could not resolve $HOME inside '{instance_name}' - using '{target}' as-is "
              "(this may fail if it relies on tilde expansion).")
        return target
    return home if target == "~" else f"{home}{target[1:]}"


def with_trailing_slash(path):
    return path if path.endswith("/") else path + "/"


def ensure_wsl_command(instance_name, command, apt_package=None):
    """Make sure `command` is on PATH inside the WSL instance, installing
    apt_package (defaults to `command`) via apt if it's missing. Returns False
    (without asking) if it can't be installed - callers should fall back."""
    validate_wsl_name(instance_name, "WSL instance name")
    check = subprocess.run(
        ["wsl", "-d", instance_name, "--", "bash", "-lc", f"command -v {shlex.quote(command)}"],
        capture_output=True,
    )
    if check.returncode == 0:
        return True

    package = apt_package or command
    step(f"Installing '{package}' inside '{instance_name}' (enables copy progress)")
    result = run(
        ["wsl", "-d", instance_name, "-u", "root", "--", "bash", "-lc",
         f"apt-get update -qq && apt-get install -y {shlex.quote(package)}"],
    )
    return result.returncode == 0


def copy_existing_working_dir(instance_name, default_source=DEFAULT_WINDOWS_SOURCE_DIR,
                              default_target=DEFAULT_WSL_WORKING_TARGET):
    validate_wsl_name(instance_name, "WSL instance name")
    step(f"Copy an existing working directory into '{instance_name}'?")

    answer = ask("Do you have an existing directory on Windows you want copied into "
                 f"'{instance_name}' for native-speed access? [y/N] ")
    if not answer.strip().lower().startswith("y"):
        return True

    source_path = None
    while source_path is None:
        entered = ask(f"Windows path to copy [{default_source}]: ").strip().strip('"')
        candidate_str = entered or default_source
        candidate = Path(candidate_str)
        if candidate.is_dir():
            source_path = candidate
            break
        print(f"'{candidate_str}' is not a directory (or not reachable from here).")
        retry = ask("Try a different path? [Y/n] ")
        if retry.strip().lower().startswith("n"):
            print("Skipping copy.")
            return True

    source = str(source_path)
    wsl_source = windows_path_to_wsl(source_path)
    target = ask(f"Target path inside '{instance_name}' [{default_target}]: ").strip()
    target = target or default_target
    target = expand_wsl_target(instance_name, target)

    check = subprocess.run(
        ["wsl", "-d", instance_name, "--", "bash", "-c", f"test -e {shlex.quote(target)}"],
    )
    if check.returncode == 0:
        answer = ask(f"'{target}' already exists inside '{instance_name}' - merge the copy "
                     f"into it? [y/N] ")
        if not answer.strip().lower().startswith("y"):
            print("Skipping copy.")
            return True

    have_rsync = ensure_wsl_command(instance_name, "rsync")

    step(f"Copying '{source}' -> '{instance_name}:{target}'")
    if have_rsync:
        # Plain incremental recursion (rsync's default): the percentage can be a
        # rough estimate early on since it's still discovering files/dirs, but
        # output starts immediately. --no-inc-recursive gives a fully accurate
        # percentage but has to silently build the whole file list first. That
        # pre-scan can take long enough to look like there is no progress at all.
        copy_cmd = (f"mkdir -p {shlex.quote(target)} && "
                    f"rsync -a --info=progress2 "
                    f"{shlex.quote(with_trailing_slash(wsl_source))} "
                    f"{shlex.quote(with_trailing_slash(target))}")
    else:
        print("rsync isn't available inside the instance - copying with `cp` instead "
              "(no progress percentage).")
        copy_cmd = (f"mkdir -p {shlex.quote(target)} && "
                    f"cp -rT {shlex.quote(wsl_source)} {shlex.quote(target)}")

    result = run(["wsl", "-d", instance_name, "--", "bash", "-c", copy_cmd])

    if result.returncode == 0:
        print(f"Copied into '{instance_name}:{target}'. Check for anything inside that "
              "hardcodes the selected Windows path (git remotes are fine, but absolute paths in "
              "config files or scripts may need updating).")
        return True
    elif result.returncode == 20:
        print("Copy cancelled.")
    elif have_rsync and result.returncode in (23, 24):
        print(f"Copy finished, but some files were skipped (rsync exit code {result.returncode}) "
              "- likely permission-restricted or system files (e.g. Windows' hidden "
              "'System Volume Information'). Check the output above for which paths were "
              "skipped; everything else should have copied fine.")
    else:
        print(f"Copy failed (exit code {result.returncode}). Check the output above for details.")
    return False


# --- 4. yazi (reuse the local installer, run inside the WSL instance) --------

def configure_yazi(instance_name, yazi_script=None, wsl_yazi_target=None,
                   wsl_working_target=DEFAULT_WSL_WORKING_TARGET):
    validate_wsl_name(instance_name, "WSL instance name")
    step(f"Install yazi inside '{instance_name}'?")

    answer = ask(f"Install yazi inside '{instance_name}' with the same setup as "
                 f"applications/yazi? [y/N] ")
    if not answer.strip().lower().startswith("y"):
        return True

    yazi_script = Path(yazi_script) if yazi_script else (
        Path(__file__).resolve().parent.parent / "yazi" / "install_yazi.py"
    )
    if not yazi_script.is_file():
        print(f"Couldn't find the yazi installer at '{yazi_script}' - skipping.")
        return False

    # install_yazi.py is a Python script; it needs python3 available inside WSL.
    if not ensure_wsl_command(instance_name, "python3"):
        print(f"python3 isn't available inside '{instance_name}' and couldn't be installed "
              "- yazi setup cannot continue. Install python3 in the instance and re-run.")
        return False

    # 'g v' -> the selected Windows drive as seen from WSL; 'g d' -> the WSL-native
    # working directory, resolved to an absolute path so yazi's `cd` does not have
    # to expand a leading '~' itself.
    if wsl_yazi_target is None:
        wsl_yazi_target = "/mnt/v"
    entered = ask(f"Path the 'g v' shortcut should jump to inside '{instance_name}' "
                  f"[{wsl_yazi_target}]: ").strip()
    gv_target = entered or wsl_yazi_target
    gd_target = expand_wsl_target(instance_name, wsl_working_target)

    wsl_script = windows_path_to_wsl(yazi_script)

    print("Note: yazi isn't in Ubuntu's default apt repos, so on a bare instance the installer "
          "bootstraps the Rust toolchain and builds yazi from source with cargo - this can take "
          "a few minutes the first time.")

    result = run(["wsl", "-d", instance_name, "--", "python3", wsl_script,
                  "--target-path", gv_target, "--extra-shortcut", "g d", gd_target])
    if result.returncode != 0:
        print(f"Yazi setup inside '{instance_name}' failed with exit code "
              f"{result.returncode}. Re-run after resolving the error above.")
        return False
    return True


# --- 5. JetBrains Gateway (native-speed IntelliJ/PyCharm/etc. against WSL) ---

def configure_jetbrains_gateway(instance_name):
    validate_wsl_name(instance_name, "WSL instance name")
    step("JetBrains Gateway")

    answer = ask("Do you use JetBrains IDEs (IntelliJ, PyCharm, WebStorm, ...) to open repos "
                 f"living inside the '{instance_name}' WSL instance? [y/N] ")
    if not answer.strip().lower().startswith("y"):
        return True

    install_answer = ask("Install JetBrains Gateway now? [y/N] ")
    if install_answer.strip().lower().startswith("y"):
        if not install_package(
            "JetBrains Gateway",
            winget_id="JetBrains.Gateway",
            manual_hint=(
                "Download it from https://www.jetbrains.com/remote-development/gateway/ "
                "if winget isn't available."
            ),
        ):
            return False
    else:
        print("Skipping install - grab it yourself from "
              "https://www.jetbrains.com/remote-development/gateway/ when you're ready.")

    print(f"""
To open a repo inside '{instance_name}' with full native performance:
  1. Launch JetBrains Gateway.
  2. Choose "WSL" as the connection type - Gateway auto-detects installed distros.
  3. Select '{instance_name}', then the repo path inside it (e.g. ~/workspace/<repo>).
  4. Pick which IDE backend to use (IntelliJ IDEA, PyCharm, ...) - Gateway downloads
     and runs it *inside* '{instance_name}', so indexing/builds happen at native
     ext4 speed.
  5. A thin client window opens on Windows - that's what you use day to day; the
     heavy lifting runs in WSL.

Avoid opening \\\\wsl.localhost\\{instance_name}\\... paths directly from a normal
Windows-installed IntelliJ - that keeps the IDE on Windows and every file access
crosses the slow WSL<->Windows bridge, which is exactly what Gateway avoids.
""")
    return True


# --- main -----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wsl-distro", default=DEFAULT_WSL_DISTRO,
                         help=f"Base WSL distro image to install "
                              f"(default: {DEFAULT_WSL_DISTRO}).")
    parser.add_argument("--wsl-instance-name", default=DEFAULT_WSL_INSTANCE_NAME,
                         help=f"Name to rename the WSL instance to "
                              f"(default: {DEFAULT_WSL_INSTANCE_NAME}).")
    parser.add_argument("--wsl-hostname", default=None,
                         help=f"Hostname to set inside the WSL instance (default: prompts, "
                              f"offering '{DEFAULT_WSL_HOSTNAME}'). Pass this to skip the prompt.")
    parser.add_argument("--windows-source-dir", default=DEFAULT_WINDOWS_SOURCE_DIR,
                              help=f"Default Windows directory offered for the WSL copy "
                                   f"(default: {DEFAULT_WINDOWS_SOURCE_DIR}).")
    parser.add_argument("--wsl-working-target", "--wsl-dev-target",
                              dest="wsl_working_target", default=DEFAULT_WSL_WORKING_TARGET,
                              help=f"Default WSL target for the copied directory and yazi's "
                                   f"'g d' shortcut (default: {DEFAULT_WSL_WORKING_TARGET}).")
    parser.add_argument("--wsl-yazi-target", default=DEFAULT_WSL_YAZI_TARGET,
                              help="Default WSL path for yazi's 'g v' shortcut. If omitted, "
                                   "the drive mount for --windows-source-dir is used.")
    parser.add_argument("--yazi-installer", default=None,
                              help="Path to install_yazi.py (default: the sibling installer "
                                   "under applications/yazi).")
    parser.add_argument("--export-tar", default=None,
                              help="Path for the WSL export archive. If omitted, "
                                   "a workspace-personal archive path under LOCALAPPDATA is used.")
    parser.add_argument("--skip-install", action="store_true",
                         help="Don't check/install/rename the distro (assume it exists).")
    parser.add_argument("--skip-hostname", action="store_true",
                         help="Don't configure a custom hostname.")
    parser.add_argument("--skip-working-copy", "--skip-dev-copy",
                         dest="skip_working_copy", action="store_true",
                         help="Don't ask about copying an existing Windows working directory.")
    parser.add_argument("--skip-yazi", action="store_true",
                         help="Don't ask about installing yazi inside the WSL instance.")
    parser.add_argument("--skip-jetbrains-gateway", action="store_true",
                         help="Don't ask about JetBrains Gateway.")
    args = parser.parse_args()

    if not IS_WINDOWS:
        print("This script sets up WSL2, which is Windows-only. Nothing to do here.")
        return 0
    try:
        validate_wsl_name(args.wsl_distro, "Base WSL distro name")
        validate_wsl_name(args.wsl_instance_name, "WSL instance name")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    wsl_setup = (
        ensure_existing_target_wsl2(args.wsl_distro, args.wsl_instance_name)
        if args.skip_install
        else configure_wsl(args.wsl_distro, args.wsl_instance_name, args.export_tar)
    )
    if wsl_setup is False:
        return 1
    wsl2_ready = wsl_setup is True

    if wsl2_ready:
        if not args.skip_hostname:
            if not configure_wsl_hostname(args.wsl_instance_name, args.wsl_hostname):
                return 1
        if not args.skip_working_copy:
            if not copy_existing_working_dir(
                args.wsl_instance_name,
                default_source=args.windows_source_dir,
                default_target=args.wsl_working_target,
            ):
                return 1
        if not args.skip_yazi:
            yazi_target = args.wsl_yazi_target
            if yazi_target is None:
                try:
                    yazi_target = windows_drive_mount(Path(args.windows_source_dir))
                except ValueError as exc:
                    print(f"Could not derive a WSL drive mount from --windows-source-dir: "
                          f"{exc}; using /mnt/v.")
                    yazi_target = "/mnt/v"
            if not configure_yazi(
                args.wsl_instance_name,
                yazi_script=args.yazi_installer,
                wsl_yazi_target=yazi_target,
                wsl_working_target=args.wsl_working_target,
            ):
                return 1
    else:
        if is_distro_installed(args.wsl_instance_name):
            print(f"\n'{args.wsl_instance_name}' is not available as WSL2 - skipping "
                  "hostname and working-directory steps.")
        else:
            print(f"\n'{args.wsl_instance_name}' isn't installed yet - skipping hostname and "
                  "working-directory steps. Finish the distro setup above, then re-run.")

    if not args.skip_jetbrains_gateway:
        if not configure_jetbrains_gateway(args.wsl_instance_name):
            return 1

    step(f"Done. Point WezTerm at '{args.wsl_instance_name}' with "
         "applications/wezterm/install_wezterm.py --shell-approach wsl.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
    except (ValueError, WslConfReadError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
