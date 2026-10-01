#!/usr/bin/env python3
"""Run dependency-free focused checks for the workspace remediation fixes.

The checks cover shell wrapper line endings and Bash syntax, installer
build/staging selection, strict PowerShell profile discovery, profile encoding
and marker handling, safe WezTerm config shapes and WSL output decoding, Yazi
TOML escaping and line endings, existing executable discovery, WSL
configuration read safety and name/path boundaries, WSL hostname and
default-user handling, and WSL rename cleanup failures, atomic marker
preflight, rerunnable Notepad++ updates, sibling-profile conflicts, existing
WSL target-user and UID validation, import ownership, persisted source-user
enforcement, namespace-wide managed-marker validation, AI shortcut migrations,
notes shortcut behavior, and mocked Windows environment broadcasts.
"""

import base64
import codecs
import contextlib
import ctypes
import importlib.util
import io
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from types import SimpleNamespace


sys.dont_write_bytecode = True

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_ROOT = WORKSPACE_ROOT / f".remediation-check-{os.getpid()}"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def powershell_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def powershell_environment(scratch):
    env = os.environ.copy()
    for key in list(env):
        if key.lower() in {"path", "pathext"}:
            del env[key]
    env["PATH"] = os.pathsep.join((str(scratch), os.environ.get("PATH", "")))
    env["PATHEXT"] = ".CMD;.EXE;.BAT;.COM"
    return env


def _bash_path(path):
    resolved = Path(path).resolve()
    if os.name == "nt" and resolved.drive:
        return f"/mnt/{resolved.drive[0].lower()}{str(resolved)[2:].replace(chr(92), '/')}"
    return str(resolved)


def check_shell_wrappers():
    attributes = WORKSPACE_ROOT / ".gitattributes"
    if not attributes.is_file():
        raise AssertionError("root .gitattributes is missing")
    attribute_text = attributes.read_text(encoding="utf-8")
    if "* text=auto" not in attribute_text or "*.sh text eol=lf" not in attribute_text:
        raise AssertionError("shell line-ending attributes are incomplete")

    wrappers = (
        WORKSPACE_ROOT / "setup.sh",
        WORKSPACE_ROOT / "clone.sh",
    )
    for wrapper in wrappers:
        data = wrapper.read_bytes()
        if b"\r" in data:
            raise AssertionError(f"{wrapper.name} is not LF-only")

    bash = shutil.which("bash")
    if not bash:
        print("Bash is not available - skipping shell syntax execution checks.")
        return
    for wrapper in wrappers:
        result = subprocess.run(
            [bash, "-n", _bash_path(wrapper)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"{wrapper.name} failed Bash syntax validation:\n"
                f"{result.stdout}\n{result.stderr}"
            )


def run_profile_helper(
    powershell,
    helper,
    target,
    scratch,
    program_files=None,
    local_app_data=None,
    pwsh_target=None,
    powershell_target=None,
    pwsh_payload=None,
    powershell_payload=None,
    pwsh_exit_code=0,
    powershell_exit_code=0,
    helper_arguments=None,
):
    fake_pwsh = scratch / "pwsh.cmd"
    fake_powershell = scratch / "powershell.cmd"
    pwsh_profiles = (
        pwsh_target or target,
        scratch / "pwsh café-current.ps1",
        scratch / "pwsh-all.ps1",
        scratch / "pwsh-all-current.ps1",
    )
    powershell_profiles = (
        powershell_target or target,
        scratch / "powershell café-current.ps1",
        scratch / "powershell-all.ps1",
        scratch / "powershell-all-current.ps1",
    )
    def profile_payload(paths):
        return base64.b64encode(
            "\n".join(str(path) for path in paths).encode("utf-8")
        ).decode("ascii")

    pwsh_payload = pwsh_payload or profile_payload(pwsh_profiles)
    powershell_payload = powershell_payload or profile_payload(powershell_profiles)
    fake_pwsh.write_text(
        "@echo off\r\n"
        + f"echo {pwsh_payload}\r\n"
        + (f"exit /b {pwsh_exit_code}\r\n" if pwsh_exit_code else ""),
        encoding="ascii",
    )
    fake_powershell.write_text(
        "@echo off\r\n"
        + f"echo {powershell_payload}\r\n"
        + (f"exit /b {powershell_exit_code}\r\n" if powershell_exit_code else ""),
        encoding="ascii",
    )
    (scratch / "claude.cmd").write_text("@echo off\r\n", encoding="ascii")

    rendered_arguments = []
    for argument in helper_arguments or ():
        value = str(argument)
        rendered_arguments.append(
            value if value.startswith("-") else powershell_literal(value)
        )
    helper_argument_text = (
        " " + " ".join(rendered_arguments) if rendered_arguments else ""
    )

    profile = scratch / "caller.ps1"
    profile.write_text(
        "$PROFILE = [pscustomobject]@{\n"
        f"    CurrentUserAllHosts = {powershell_literal(target)}\n"
        f"    CurrentUserCurrentHost = {powershell_literal(scratch / 'current.ps1')}\n"
        f"    AllUsersAllHosts = {powershell_literal(scratch / 'all.ps1')}\n"
        f"    AllUsersCurrentHost = {powershell_literal(scratch / 'all-current.ps1')}\n"
        "}\n"
        "$env:PATHEXT = '.CMD;.EXE;.BAT;.COM'\n"
        f"$env:PATH = {powershell_literal(str(scratch))} + ';' + "
        "((($env:PATH -split ';') | Where-Object { $_ -and ($_ -ne $PSHOME) }) "
        "-join ';')\n"
        f"& {powershell_literal(helper)} -Quiet{helper_argument_text}\n",
        encoding="ascii",
    )

    env = powershell_environment(scratch)
    if program_files is not None:
        env["ProgramFiles"] = str(program_files)
        env["ProgramFiles(x86)"] = str(program_files)
    if local_app_data is not None:
        env["LOCALAPPDATA"] = str(local_app_data)
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(profile),
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def check_profile_helper_behavior(powershell):
    scripts = WORKSPACE_ROOT / "scripts"
    helpers = (
        scripts / "install-ai-alias.ps1",
        scripts / "install-ll-function.ps1",
        scripts / "install-np-alias.ps1",
    )
    expected_new_bom = Path(powershell).stem.lower() == "powershell"
    profile_text = "# existing café profile\r\n"
    encodings = (
        ("utf-8", profile_text.encode("utf-8")),
        ("utf-8-bom", codecs.BOM_UTF8 + profile_text.encode("utf-8")),
        ("utf-16-le", codecs.BOM_UTF16_LE + profile_text.encode("utf-16-le")),
        ("utf-16-be", codecs.BOM_UTF16_BE + profile_text.encode("utf-16-be")),
    )

    for helper in helpers:
        helper_name = helper.stem
        for encoding_name, original in encodings:
            fixture = SCRATCH_ROOT / f"{helper_name}-{encoding_name}"
            fixture.mkdir()
            target = fixture / "profile.ps1"
            target.write_bytes(original)
            program_files = fixture / "Program Files"
            (program_files / "Notepad++").mkdir(parents=True)
            (program_files / "Notepad++" / "notepad++.exe").write_bytes(b"")
            result = run_profile_helper(
                powershell, helper, target, fixture, program_files=program_files
            )
            if result.returncode != 0:
                raise AssertionError(
                    f"{helper.name} failed for {encoding_name} profile:\n"
                    f"{result.stdout}\n{result.stderr}"
                )
            written = target.read_bytes()
            expected_bom = {
                "utf-8": b"",
                "utf-8-bom": codecs.BOM_UTF8,
                "utf-16-le": codecs.BOM_UTF16_LE,
                "utf-16-be": codecs.BOM_UTF16_BE,
            }[encoding_name]
            if not written.startswith(expected_bom):
                raise AssertionError(
                    f"{helper.name} did not preserve the {encoding_name} BOM"
                )
            if encoding_name == "utf-8":
                decoded = written.decode("utf-8")
            elif encoding_name == "utf-8-bom":
                decoded = written.decode("utf-8-sig")
            elif encoding_name == "utf-16-le":
                decoded = written.decode("utf-16")
            else:
                decoded = written[2:].decode("utf-16-be")
            if "existing café profile" not in decoded:
                raise AssertionError(
                    f"{helper.name} did not preserve {encoding_name} profile content"
                )
            if "workspace-personal:" not in decoded:
                raise AssertionError(
                    f"{helper.name} did not add its managed block"
                )

        new_fixture = SCRATCH_ROOT / f"{helper_name}-new"
        new_fixture.mkdir()
        new_target = new_fixture / "profile.ps1"
        program_files = new_fixture / "Program Files"
        (program_files / "Notepad++").mkdir(parents=True)
        (program_files / "Notepad++" / "notepad++.exe").write_bytes(b"")
        result = run_profile_helper(
            powershell, helper, new_target, new_fixture, program_files=program_files
        )
        if result.returncode != 0:
            raise AssertionError(
                f"{helper.name} failed for a new profile:\n"
                f"{result.stdout}\n{result.stderr}"
            )
        new_bytes = new_target.read_bytes()
        has_bom = new_bytes.startswith(codecs.BOM_UTF8)
        if has_bom != expected_new_bom:
            edition = "Windows PowerShell" if expected_new_bom else "pwsh"
            raise AssertionError(
                f"{helper.name} did not preserve {edition} new-profile behavior"
            )

        failure_fixture = SCRATCH_ROOT / f"{helper_name}-failure"
        failure_fixture.mkdir()
        failure_target = failure_fixture / "profile.ps1"
        failure_target.mkdir()
        program_files = failure_fixture / "Program Files"
        (program_files / "Notepad++").mkdir(parents=True)
        (program_files / "Notepad++" / "notepad++.exe").write_bytes(b"")
        result = run_profile_helper(
            powershell,
            helper,
            failure_target,
            failure_fixture,
            program_files=program_files,
        )
        if result.returncode == 0:
            raise AssertionError(
                f"{helper.name} swallowed a profile write failure"
            )

        invalid_fixture = SCRATCH_ROOT / f"{helper_name}-invalid"
        invalid_fixture.mkdir()
        invalid_target = invalid_fixture / "profile.ps1"
        invalid_bytes = b"\xff\xfe\xfa"
        invalid_target.write_bytes(invalid_bytes)
        program_files = invalid_fixture / "Program Files"
        (program_files / "Notepad++").mkdir(parents=True)
        (program_files / "Notepad++" / "notepad++.exe").write_bytes(b"")
        result = run_profile_helper(
            powershell,
            helper,
            invalid_target,
            invalid_fixture,
            program_files=program_files,
        )
        if result.returncode == 0:
            raise AssertionError(
                f"{helper.name} accepted an undecodable profile"
            )
        if invalid_target.read_bytes() != invalid_bytes:
            raise AssertionError(
                f"{helper.name} changed an undecodable profile"
            )


def check_ai_shortcut_migrations(powershell):
    scripts = WORKSPACE_ROOT / "scripts"
    ai_helper = scripts / "install-ai-alias.ps1"
    legacy_profiles = (
        (
            "team-logistics-components",
            "# >>> team-logistics-components: ai alias >>>\n"
            "function ai {\n"
            "    & copilot @Args\n"
            "}\n"
            "# <<< team-logistics-components: ai alias <<<\n",
        ),
        (
            "powershell-setup",
            "# >>> powershell-setup: ai >>>\n"
            "function ai {\n"
            "    & copilot @Args\n"
            "}\n"
            "# <<< powershell-setup: ai <<<\n",
        ),
    )
    for name, legacy in legacy_profiles:
        fixture = SCRATCH_ROOT / f"ai-alias-{name}-migration"
        fixture.mkdir()
        target = fixture / "profile.ps1"
        target.write_text(legacy, encoding="utf-8")
        result = run_profile_helper(powershell, ai_helper, target, fixture)
        if result.returncode != 0:
            raise AssertionError(
                f"ai helper failed to migrate {name}:\n"
                f"{result.stdout}\n{result.stderr}"
            )
        content = target.read_text(encoding="utf-8-sig")
        if "workspace-personal: ai alias" not in content:
            raise AssertionError(f"ai helper did not install its managed block for {name}")
        if "& claude @Args" not in content or "& copilot @Args" in content:
            raise AssertionError(f"ai helper retained the old CLI for {name}")
        if f"{name}: ai" in content:
            raise AssertionError(f"ai helper retained the legacy {name} marker")

    conflict_fixture = SCRATCH_ROOT / "ai-alias-legacy-unmanaged-conflict"
    conflict_fixture.mkdir()
    conflict_target = conflict_fixture / "profile.ps1"
    conflict_content = (
        legacy_profiles[0][1]
        + "\nfunction ai {\n"
        "    Write-Host 'handwritten'\n"
        "}\n"
    )
    conflict_target.write_text(conflict_content, encoding="utf-8")
    before = conflict_target.read_bytes()
    result = run_profile_helper(
        powershell, ai_helper, conflict_target, conflict_fixture
    )
    if result.returncode != 0:
        raise AssertionError(
            f"ai helper failed while preserving an unmanaged command:\n"
            f"{result.stdout}\n{result.stderr}"
        )
    if conflict_target.read_bytes() != before:
        raise AssertionError(
            "ai helper overwrote an unmanaged ai command outside a legacy block"
        )


def check_profile_helper_discovery_failures(powershell):
    scripts = WORKSPACE_ROOT / "scripts"
    helpers = (
        scripts / "install-ai-alias.ps1",
        scripts / "install-ll-function.ps1",
        scripts / "install-np-alias.ps1",
    )
    malformed = base64.b64encode(b"V:\\profiles\\one.ps1\nV:\\profiles\\two.ps1\n").decode(
        "ascii"
    )
    cases = (
        ("pwsh-nonzero", None, None, 1, 0),
        ("powershell-nonzero", None, None, 0, 1),
        ("pwsh-malformed", malformed, None, 0, 0),
        ("powershell-malformed", None, malformed, 0, 0),
        ("pwsh-invalid-base64", "not-base64", None, 0, 0),
    )
    for helper in helpers:
        for case_name, pwsh_payload, powershell_payload, pwsh_exit, powershell_exit in cases:
            fixture = SCRATCH_ROOT / f"{helper.stem}-discovery-{case_name}"
            fixture.mkdir()
            target = fixture / "profile.ps1"
            program_files = fixture / "Program Files"
            npp = program_files / "Notepad++" / "notepad++.exe"
            npp.parent.mkdir(parents=True)
            npp.write_bytes(b"")
            result = run_profile_helper(
                powershell,
                helper,
                target,
                fixture,
                program_files=program_files,
                pwsh_payload=pwsh_payload,
                powershell_payload=powershell_payload,
                pwsh_exit_code=pwsh_exit,
                powershell_exit_code=powershell_exit,
            )
            if result.returncode == 0:
                raise AssertionError(
                    f"{helper.name} accepted {case_name} profile discovery:\n"
                    f"{result.stdout}\n{result.stderr}"
                )
            if target.exists():
                raise AssertionError(
                    f"{helper.name} wrote a profile after {case_name} discovery failed"
                )


def check_shell_extra_sibling_conflicts(powershell):
    scripts = WORKSPACE_ROOT / "scripts"
    helpers = (
        ("install-ai-alias.ps1", "function ai { }"),
        ("install-ll-function.ps1", "function ll { }"),
        ("install-np-alias.ps1", "Set-Alias np notepad"),
    )
    for helper_name, conflict in helpers:
        fixture = SCRATCH_ROOT / f"{Path(helper_name).stem}-sibling-conflict"
        fixture.mkdir()
        target = fixture / "profile.ps1"
        sibling = fixture / "Microsoft.VSCode_profile.ps1"
        sibling.write_text(conflict + "\r\n", encoding="utf-8")
        program_files = fixture / "Program Files"
        npp = program_files / "Notepad++" / "notepad++.exe"
        npp.parent.mkdir(parents=True)
        npp.write_bytes(b"")

        result = run_profile_helper(
            powershell,
            scripts / helper_name,
            target,
            fixture,
            program_files=program_files,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"{helper_name} failed while scanning a sibling profile:\n"
                f"{result.stdout}\n{result.stderr}"
            )
        if target.exists() and "workspace-personal:" in target.read_text(
            encoding="utf-8-sig"
        ):
            raise AssertionError(
                f"{helper_name} ignored a conflicting sibling profile"
            )


def check_shell_extra_all_profile_conflicts(powershell):
    scripts = WORKSPACE_ROOT / "scripts"
    helpers = (
        ("install-ai-alias.ps1", "function ai { }"),
        ("install-ll-function.ps1", "function ll { }"),
        ("install-np-alias.ps1", "Set-Alias np notepad"),
    )
    for helper_name, conflict in helpers:
        fixture = SCRATCH_ROOT / f"{Path(helper_name).stem}-all-profile-conflict"
        fixture.mkdir()
        target = fixture / "profile.ps1"
        (fixture / "pwsh-all.ps1").write_text(
            conflict + "\r\n", encoding="utf-8"
        )
        program_files = fixture / "Program Files"
        npp = program_files / "Notepad++" / "notepad++.exe"
        npp.parent.mkdir(parents=True)
        npp.write_bytes(b"")

        result = run_profile_helper(
            powershell,
            scripts / helper_name,
            target,
            fixture,
            program_files=program_files,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"{helper_name} failed while scanning all profile properties:\n"
                f"{result.stdout}\n{result.stderr}"
            )
        if target.exists() and "workspace-personal:" in target.read_text(
            encoding="utf-8-sig"
        ):
            raise AssertionError(
                f"{helper_name} ignored a conflicting non-target profile"
            )


def check_shell_extra_atomic_marker_preflight(powershell):
    scripts = WORKSPACE_ROOT / "scripts"
    helpers = (
        scripts / "install-ai-alias.ps1",
        scripts / "install-ll-function.ps1",
        scripts / "install-np-alias.ps1",
    )
    for helper in helpers:
        fixture = SCRATCH_ROOT / f"{helper.stem}-atomic"
        profiles = fixture / "profiles"
        profiles.mkdir(parents=True)
        first = profiles / "first.ps1"
        second = profiles / "second.ps1"
        malformed = profiles / "malformed.ps1"
        first.write_text("# first profile\r\n", encoding="utf-8")
        second.write_text("# second profile\r\n", encoding="utf-8")
        malformed.write_text(
            "# >>> workspace-personal: malformed >>>\r\n"
            "# unfinished managed block\r\n",
            encoding="utf-8",
        )
        before = {
            path: path.read_bytes() for path in (first, second, malformed)
        }
        program_files = fixture / "Program Files"
        npp = program_files / "Notepad++" / "notepad++.exe"
        npp.parent.mkdir(parents=True)
        npp.write_bytes(b"")

        result = run_profile_helper(
            powershell,
            helper,
            first,
            fixture,
            program_files=program_files,
            pwsh_target=second,
            powershell_target=malformed,
        )
        if result.returncode == 0:
            raise AssertionError(
                f"{helper.name} accepted malformed markers during preflight"
            )
        for path, original in before.items():
            if path.read_bytes() != original:
                raise AssertionError(
                    f"{helper.name} changed {path.name} before marker preflight failed"
                )


def check_np_alias_rerun(powershell):
    helper = WORKSPACE_ROOT / "scripts" / "install-np-alias.ps1"
    fixture = SCRATCH_ROOT / "np-rerun"
    fixture.mkdir()
    target = fixture / "profile.ps1"
    local_app_data = fixture / "LocalAppData"
    first_npp = local_app_data / "Programs" / "Notepad++" / "notepad++.exe"
    first_npp.parent.mkdir(parents=True)
    first_npp.write_bytes(b"")

    first_result = run_profile_helper(
        powershell, helper, target, fixture, local_app_data=local_app_data
    )
    if first_result.returncode != 0:
        raise AssertionError(
            f"initial np installation failed:\n{first_result.stdout}\n{first_result.stderr}"
        )
    first_content = target.read_text(encoding="utf-8-sig")
    path_line = next(
        (line for line in first_content.splitlines(keepends=True) if "& '" in line),
        None,
    )
    if path_line is None:
        raise AssertionError("initial np block did not contain an executable path")
    stale_path = r"C:\stale\Notepad++\notepad++.exe"
    stale_line = re.sub(
        r"& '.*?' @Args",
        lambda _match: f"& '{stale_path}' @Args",
        path_line,
    )
    target.write_bytes(
        codecs.BOM_UTF8 + first_content.replace(path_line, stale_line, 1).encode("utf-8")
    )

    second_result = run_profile_helper(
        powershell, helper, target, fixture, local_app_data=local_app_data
    )
    if second_result.returncode != 0:
        raise AssertionError(
            f"rerun np installation failed:\n"
            f"{second_result.stdout}\n{second_result.stderr}"
        )
    second_content = target.read_text(encoding="utf-8-sig")
    if stale_path in second_content:
        raise AssertionError("rerun np block retained the stale Notepad++ path")
    if path_line.split("& '", 1)[1].split("' @Args", 1)[0] not in second_content:
        raise AssertionError("rerun np block did not restore the discovered path")


def check_notes_helper(powershell):
    if Path(powershell).stem.lower() != "pwsh":
        print("PowerShell 7 is not selected - skipping notes helper behavior checks.")
        return

    scripts = WORKSPACE_ROOT / "scripts"
    helper = scripts / "install-notes-function.ps1"
    manifest = (scripts / "shell-extras.psd1.ps1").read_text(encoding="utf-8")
    if ("Name        = 'notes'" not in manifest or
            "install-notes-function.ps1" not in manifest):
        raise AssertionError("notes helper is missing from the shell-extra manifest")

    fixture = SCRATCH_ROOT / "notes-helper"
    fixture.mkdir()
    notes_path = fixture / "notes with spaces"
    notes_path.mkdir()
    target = fixture / "profile.ps1"
    target.write_text("# existing notes profile\r\n", encoding="utf-8")
    legacy_profile = fixture / "windows-powershell-profile.ps1"
    legacy_profile.write_text("# legacy profile\r\n", encoding="utf-8")
    legacy_before = legacy_profile.read_bytes()

    result = run_profile_helper(
        powershell,
        helper,
        target,
        fixture,
        helper_arguments=("-NotesPath", str(notes_path)),
    )
    if result.returncode != 0:
        raise AssertionError(
            f"notes helper failed to install:\n{result.stdout}\n{result.stderr}"
        )

    installed = target.read_text(encoding="utf-8")
    resolved_notes_path = str(notes_path.resolve())
    escaped_notes_path = resolved_notes_path.replace("'", "''")
    expected_command = f"Set-Location -LiteralPath '{escaped_notes_path}'"
    if "function notes" not in installed or expected_command not in installed:
        raise AssertionError("notes helper did not write the expected function")
    if installed.count("# >>> workspace-personal: notes function >>>") != 1:
        raise AssertionError("notes helper wrote duplicate managed blocks")
    if legacy_profile.read_bytes() != legacy_before:
        raise AssertionError("notes helper changed a non-PowerShell-7 profile")

    invocation = fixture / "invoke-notes.ps1"
    invocation.write_text(
        f". {powershell_literal(target)}\n"
        "notes\n"
        "$actual = [System.IO.Path]::GetFullPath((Get-Location).Path)\n"
        f"$expected = [System.IO.Path]::GetFullPath({powershell_literal(resolved_notes_path)})\n"
        "if (-not [string]::Equals($actual, $expected, "
        "[System.StringComparison]::OrdinalIgnoreCase)) { throw \"notes did not change location\" }\n",
        encoding="utf-8",
    )
    invocation_result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(invocation),
        ],
        capture_output=True,
        text=True,
    )
    if invocation_result.returncode != 0:
        raise AssertionError(
            f"notes function did not change location:\n"
            f"{invocation_result.stdout}\n{invocation_result.stderr}"
        )

    updated_notes_path = fixture / "updated notes"
    updated_notes_path.mkdir()
    second_result = run_profile_helper(
        powershell,
        helper,
        target,
        fixture,
        helper_arguments=("-NotesPath", str(updated_notes_path)),
    )
    if second_result.returncode != 0:
        raise AssertionError(
            f"notes helper failed to update:\n"
            f"{second_result.stdout}\n{second_result.stderr}"
        )
    updated = target.read_text(encoding="utf-8")
    if str(updated_notes_path.resolve()) not in updated:
        raise AssertionError("notes helper did not update the configured path")
    if resolved_notes_path in updated:
        raise AssertionError("notes helper retained the previous configured path")
    if updated.count("# >>> workspace-personal: notes function >>>") != 1:
        raise AssertionError("notes helper duplicated its managed block on rerun")

    missing_fixture = SCRATCH_ROOT / "notes-helper-missing"
    missing_fixture.mkdir()
    missing_target = missing_fixture / "profile.ps1"
    missing_target.write_bytes(b"# unchanged\r\n")
    missing_before = missing_target.read_bytes()
    missing_result = run_profile_helper(
        powershell,
        helper,
        missing_target,
        missing_fixture,
        helper_arguments=("-NotesPath", str(missing_fixture / "not-found")),
    )
    if missing_result.returncode != 0:
        raise AssertionError(
            f"notes helper failed while skipping a missing path:\n"
            f"{missing_result.stdout}\n{missing_result.stderr}"
        )
    if missing_target.read_bytes() != missing_before:
        raise AssertionError("notes helper changed a profile for a missing path")

    conflict_fixture = SCRATCH_ROOT / "notes-helper-conflict"
    conflict_fixture.mkdir()
    conflict_target = conflict_fixture / "profile.ps1"
    conflict_target.write_text("function notes { }\r\n", encoding="utf-8")
    conflict_before = conflict_target.read_bytes()
    conflict_notes = conflict_fixture / "notes"
    conflict_notes.mkdir()
    conflict_result = run_profile_helper(
        powershell,
        helper,
        conflict_target,
        conflict_fixture,
        helper_arguments=("-NotesPath", str(conflict_notes)),
    )
    if conflict_result.returncode != 0:
        raise AssertionError(
            f"notes helper failed while preserving a conflict:\n"
            f"{conflict_result.stdout}\n{conflict_result.stderr}"
        )
    if conflict_target.read_bytes() != conflict_before:
        raise AssertionError("notes helper overwrote an unmanaged notes function")
    if "workspace-personal:" in conflict_target.read_text(encoding="utf-8"):
        raise AssertionError("notes helper added a managed block despite a conflict")


def check_yazi_atomic_marker_preflight():
    module = load_module(
        "workspace_personal_yazi_atomic_markers",
        WORKSPACE_ROOT / "applications" / "yazi" / "install_yazi.py",
    )
    valid = SCRATCH_ROOT / "yazi-atomic-valid.ps1"
    malformed = SCRATCH_ROOT / "yazi-atomic-malformed.ps1"
    valid.write_text("# user profile\r\n", encoding="utf-8")
    malformed.write_text(
        "# >>> yazi-setup: y function >>>\r\n"
        "# unfinished managed block\r\n",
        encoding="utf-8",
    )
    before = valid.read_bytes()
    original_paths = module.shell_wrapper_paths
    try:
        module.shell_wrapper_paths = lambda: [valid, malformed]
        try:
            module.configure_cd_on_exit()
        except module.ProfileMarkerError:
            pass
        else:
            raise AssertionError("Yazi accepted malformed markers during preflight")
    finally:
        module.shell_wrapper_paths = original_paths
    if valid.read_bytes() != before:
        raise AssertionError("Yazi wrote a profile before marker preflight failed")


def check_wezterm_legacy_config():
    module = load_module(
        "workspace_personal_wezterm",
        WORKSPACE_ROOT / "applications" / "wezterm" / "install_wezterm.py",
    )
    legacy_configs = (
        "return {\n  default_prog = { 'cmd.exe' },\n}\n",
        "return ({\n  default_prog = { 'cmd.exe' },\n})\n",
        "return -- the table starts on the next line\n"
        "{\n  default_prog = { 'cmd.exe' },\n}\n",
        "return\n"
        "--[[ a multiline comment ]]\n"
        "{\n  default_prog = { 'cmd.exe' },\n}\n",
        "return -- the table is parenthesized\n"
        "({\n  default_prog = { 'cmd.exe' },\n})\n",
    )
    for index, source in enumerate(legacy_configs):
        config = SCRATCH_ROOT / f"wezterm-legacy-{index}.lua"
        config.write_text(source, encoding="utf-8")
        before = config.read_bytes()
        try:
            module.upsert_marked_block(
                config,
                "shell-approach",
                ["config.default_prog = { 'pwsh.exe' }"],
            )
        except module.UnsupportedConfigFormatError:
            pass
        else:
            raise AssertionError("returned-table WezTerm config was not refused")
        if config.read_bytes() != before:
            raise AssertionError("legacy WezTerm config changed after refusal")

    unsafe_configs = (
        "local config = {}\n"
        "return config -- comment\n",
        "local wezterm = require 'wezterm'\n"
        "local config = wezterm.config_builder()\n"
        "return config\n"
        "config.default_prog = { 'cmd.exe' }\n",
    )
    for index, source in enumerate(unsafe_configs):
        config = SCRATCH_ROOT / f"wezterm-unsafe-{index}.lua"
        config.write_text(source, encoding="utf-8")
        before = config.read_bytes()
        try:
            module.upsert_marked_block(
                config,
                "shell-approach",
                ["config.default_prog = { 'pwsh.exe' }"],
            )
        except module.UnsupportedConfigFormatError:
            pass
        else:
            raise AssertionError("unsafe WezTerm config was not refused")
        if config.read_bytes() != before:
            raise AssertionError("unsafe WezTerm config changed after refusal")

    empty = SCRATCH_ROOT / "wezterm-empty.lua"
    empty.write_text("", encoding="utf-8")
    module.upsert_marked_block(
        empty,
        "shell-approach",
        ["config.default_prog = { 'pwsh.exe' }"],
    )
    empty_content = empty.read_text(encoding="utf-8")
    for required in (
        "local wezterm = require 'wezterm'",
        "local config = wezterm.config_builder()",
        "config.default_prog = { 'pwsh.exe' }",
        "return config",
    ):
        if required not in empty_content:
            raise AssertionError(f"empty WezTerm config lacks skeleton line: {required}")
    if empty_content.index(module.MARKER_BEGIN.format(name="shell-approach")) > \
            empty_content.index("return config"):
        raise AssertionError("empty WezTerm config placed the managed block after return")

    builder = SCRATCH_ROOT / "wezterm-builder.lua"
    builder.write_text(
        "local wezterm = require 'wezterm'\n"
        "local config = wezterm.config_builder()\n"
        "return config -- keep this comment\n",
        encoding="utf-8",
    )
    module.upsert_marked_block(
        builder,
        "shell-approach",
        ["config.default_prog = { 'pwsh.exe' }"],
    )
    builder_content = builder.read_text(encoding="utf-8")
    if builder_content.index(module.MARKER_BEGIN.format(name="shell-approach")) > \
            builder_content.index("return config"):
        raise AssertionError("managed WezTerm block was appended after return")

    parenthesized_builder = SCRATCH_ROOT / "wezterm-parenthesized-builder.lua"
    parenthesized_builder.write_text(
        "local wezterm = require 'wezterm'\n"
        "local config = wezterm.config_builder()\n"
        "return -- keep this comment\n"
        "(config)\n",
        encoding="utf-8",
    )
    module.upsert_marked_block(
        parenthesized_builder,
        "shell-approach",
        ["config.default_prog = { 'pwsh.exe' }"],
    )
    parenthesized_content = parenthesized_builder.read_text(encoding="utf-8")
    if parenthesized_content.index(module.MARKER_BEGIN.format(name="shell-approach")) > \
            parenthesized_content.index("(config)"):
        raise AssertionError("parenthesized builder shape was not recognized")

    for index, return_statement in enumerate(("return config;\n", "return (config);\n")):
        semicolon_builder = SCRATCH_ROOT / f"wezterm-semicolon-builder-{index}.lua"
        semicolon_builder.write_text(
            "local wezterm = require 'wezterm'\n"
            "local config = wezterm.config_builder()\n"
            f"{return_statement}",
            encoding="utf-8",
        )
        module.upsert_marked_block(
            semicolon_builder,
            "shell-approach",
            ["config.default_prog = { 'pwsh.exe' }"],
        )
        semicolon_content = semicolon_builder.read_text(encoding="utf-8")
        if module.MARKER_BEGIN.format(name="shell-approach") not in semicolon_content:
            raise AssertionError("terminal-semicolon builder shape was rejected")

    executable_after_semicolon = SCRATCH_ROOT / "wezterm-semicolon-unsafe.lua"
    executable_after_semicolon.write_text(
        "local wezterm = require 'wezterm'\n"
        "local config = wezterm.config_builder()\n"
        "return config; config.default_prog = { 'cmd.exe' }\n",
        encoding="utf-8",
    )
    before = executable_after_semicolon.read_bytes()
    try:
        module.upsert_marked_block(
            executable_after_semicolon,
            "shell-approach",
            ["config.default_prog = { 'pwsh.exe' }"],
        )
    except module.UnsupportedConfigFormatError:
        pass
    else:
        raise AssertionError("executable tokens after return semicolon were accepted")
    if executable_after_semicolon.read_bytes() != before:
        raise AssertionError("unsafe semicolon WezTerm config changed after refusal")

    dangerous_name = "dev's\\qa\nprod"
    original_is_windows = module.IS_WINDOWS
    try:
        module.IS_WINDOWS = True
        expected_literal = module.lua_string_literal("WSL:" + dangerous_name)
        approach_line = module.shell_approach_lines("wsl", dangerous_name)[0]
        if approach_line != f"config.default_domain = {expected_literal}":
            raise AssertionError("WSL instance name was not Lua-escaped")
        switching_lines = module.shell_switching_lines([dangerous_name], True)
        if not any(
            module.lua_string_literal("WSL: " + dangerous_name) in line
            and module.lua_string_literal("WSL:" + dangerous_name) in line
            for line in switching_lines
        ):
            raise AssertionError("WSL menu name was not Lua-escaped")
    finally:
        module.IS_WINDOWS = original_is_windows

    bom_only = SCRATCH_ROOT / "wezterm-bom-only.lua"
    bom_only.write_bytes(codecs.BOM_UTF8)
    module.upsert_marked_block(
        bom_only,
        "shell-approach",
        ["config.default_prog = { 'pwsh.exe' }"],
    )
    if "local config = wezterm.config_builder()" not in \
            bom_only.read_text(encoding="utf-8-sig"):
        raise AssertionError("BOM-only WezTerm config was not initialized")

    unrelated = SCRATCH_ROOT / "wezterm-unrelated.lua"
    unrelated.write_text(
        "local text = 'return {'\n"
        "-- return { this is only a comment\n"
        "local wezterm = require 'wezterm'\n"
        "local config = wezterm.config_builder()\n"
        "function helper()\n"
        "  return -- a helper function table\n"
        "  { value = true }\n"
        "end\n"
        "return config\n",
        encoding="utf-8",
    )
    module.upsert_marked_block(
        unrelated,
        "shell-approach",
        ["config.default_prog = { 'pwsh.exe' }"],
    )
    if module.MARKER_BEGIN.format(name="shell-approach") not in unrelated.read_text(
        encoding="utf-8"
    ):
        raise AssertionError("unrelated WezTerm text was falsely rejected")


def check_powershell_application_profile_encoding():
    modules = (
        (
            "powershell",
            load_module(
                "workspace_personal_powershell_encoding",
                WORKSPACE_ROOT / "applications" / "powershell" / "install_powershell.py",
            ),
        ),
        (
            "yazi",
            load_module(
                "workspace_personal_yazi_encoding",
                WORKSPACE_ROOT / "applications" / "yazi" / "install_yazi.py",
            ),
        ),
    )
    profile_text = "# existing café profile\r\n"
    encodings = (
        ("utf-8", b"", "utf-8"),
        ("utf-8-bom", codecs.BOM_UTF8, "utf-8"),
        ("utf-16-le", codecs.BOM_UTF16_LE, "utf-16-le"),
        ("utf-16-be", codecs.BOM_UTF16_BE, "utf-16-be"),
        ("utf-32-le", codecs.BOM_UTF32_LE, "utf-32-le"),
        ("utf-32-be", codecs.BOM_UTF32_BE, "utf-32-be"),
    )

    for module_name, module in modules:
        for encoding_name, bom, codec in encodings:
            profile = SCRATCH_ROOT / f"{module_name}-application-{encoding_name}.ps1"
            profile.write_bytes(bom + profile_text.encode(codec))
            if module_name == "powershell":
                module.upsert_ps_block(
                    profile,
                    "encoding-check",
                    "Write-Output 'encoding check'",
                )
            else:
                module.merge_marker_block(
                    profile,
                    "encoding-check",
                    "Write-Output 'encoding check'",
                    profile=True,
                )

            written = profile.read_bytes()
            if not written.startswith(bom):
                raise AssertionError(
                    f"{module_name} did not preserve the {encoding_name} BOM"
                )
            if bom and written[len(bom):].startswith(bom):
                raise AssertionError(
                    f"{module_name} wrote a duplicate {encoding_name} BOM"
                )
            decoded = written[len(bom):].decode(codec)
            if "existing café profile" not in decoded:
                raise AssertionError(
                    f"{module_name} did not preserve {encoding_name} profile content"
                )
            if "encoding check" not in decoded:
                raise AssertionError(
                    f"{module_name} did not add its managed block to {encoding_name}"
                )
            if module.profile_encoding(written) != (
                "utf-8-sig" if encoding_name == "utf-8-bom" else encoding_name
            ):
                raise AssertionError(
                    f"{module_name} did not retain exact {encoding_name} detection"
                )

        new_profile = SCRATCH_ROOT / f"{module_name}-application-new.ps1"
        if module_name == "powershell":
            module.upsert_ps_block(
                new_profile,
                "encoding-check",
                "Write-Output 'encoding check'",
            )
        else:
            module.merge_marker_block(
                new_profile,
                "encoding-check",
                "Write-Output 'encoding check'",
                profile=True,
            )
        if not new_profile.read_bytes().startswith(codecs.BOM_UTF8):
            raise AssertionError(
                f"{module_name} changed the safe default encoding for new profiles"
            )


def check_application_profile_conflicts():
    powershell = load_module(
        "workspace_personal_powershell_profile_conflicts",
        WORKSPACE_ROOT / "applications" / "powershell" / "install_powershell.py",
    )
    powershell_target = SCRATCH_ROOT / "powershell-conflict-target.ps1"
    powershell_scan = SCRATCH_ROOT / "powershell-conflict-all-users.ps1"
    powershell_scan.write_text("function ll { }\n", encoding="utf-8")
    powershell.upsert_ps_block(
        powershell_target,
        "ll",
        "function ll { }",
        conflict_pattern=r"(?im)^\s*function\s+ll\b",
        scan_paths=[powershell_scan],
    )
    if powershell_target.exists():
        raise AssertionError("PowerShell installer ignored a non-target profile conflict")

    yazi = load_module(
        "workspace_personal_yazi_profile_conflicts",
        WORKSPACE_ROOT / "applications" / "yazi" / "install_yazi.py",
    )
    yazi_target = SCRATCH_ROOT / "yazi-conflict-target.ps1"
    yazi_scan = SCRATCH_ROOT / "yazi-conflict-current-host.ps1"
    yazi_scan.write_text("function global:y { }\n", encoding="utf-8")
    yazi.merge_marker_block(
        yazi_target,
        "y function",
        yazi.POWERSHELL_Y_FUNCTION,
        conflict_pattern=yazi.POWERSHELL_Y_CONFLICT_PATTERN,
        scan_paths=[yazi_scan],
        profile=True,
    )
    if yazi_target.exists():
        raise AssertionError("Yazi installer ignored a non-target profile conflict")


def check_powershell_malformed_marker():
    module = load_module(
        "workspace_personal_powershell_markers",
        WORKSPACE_ROOT / "applications" / "powershell" / "install_powershell.py",
    )
    profile = SCRATCH_ROOT / "powershell-malformed-marker.ps1"
    profile.write_text(
        "# >>> powershell-setup: ai >>>\n"
        "# user content after an unfinished managed block\n",
        encoding="utf-8",
    )
    before = profile.read_bytes()
    try:
        module.upsert_ps_block(profile, "ai", "function ai {}")
    except module.ProfileMarkerError:
        pass
    else:
        raise AssertionError("unbalanced PowerShell marker was not refused")
    if profile.read_bytes() != before:
        raise AssertionError("malformed PowerShell profile changed after refusal")


def check_powershell_global_marker_preflight():
    module = load_module(
        "workspace_personal_powershell_global_markers",
        WORKSPACE_ROOT / "applications" / "powershell" / "install_powershell.py",
    )
    valid = SCRATCH_ROOT / "powershell-global-valid.ps1"
    malformed = SCRATCH_ROOT / "powershell-global-malformed.ps1"
    valid.write_text("# user content\r\n", encoding="utf-8")
    malformed.write_text(
        "# >>> powershell-setup: unrelated >>>\r\n"
        "# user content after an unfinished block\r\n",
        encoding="utf-8",
    )
    before = valid.read_bytes()
    try:
        module.preflight_profile_markers([valid, malformed])
    except module.ProfileMarkerError:
        pass
    else:
        raise AssertionError("global PowerShell marker preflight accepted malformed input")
    if valid.read_bytes() != before:
        raise AssertionError("global marker preflight changed a profile before refusal")


def check_yazi_conflict_forms():
    module = load_module(
        "workspace_personal_yazi",
        WORKSPACE_ROOT / "applications" / "yazi" / "install_yazi.py",
    )
    powershell_forms = (
        "Set-Alias -Name y Get-ChildItem",
        "New-Alias y Get-ChildItem",
        "function global:y { }",
    )
    for form in powershell_forms:
        if not re.search(module.POWERSHELL_Y_CONFLICT_PATTERN, form, re.MULTILINE):
            raise AssertionError(f"PowerShell y conflict was missed: {form}")
    if re.search(
        module.POWERSHELL_Y_CONFLICT_PATTERN,
        "function yazi { }\nSet-Alias -Name yy Get-ChildItem",
        re.MULTILINE,
    ):
        raise AssertionError("unrelated PowerShell command was treated as y")

    posix_forms = (
        "function y {",
        "function y() {",
        "y() {",
        "alias y='yazi'",
    )
    for form in posix_forms:
        if not re.search(module.POSIX_Y_CONFLICT_PATTERN, form, re.MULTILINE):
            raise AssertionError(f"POSIX y conflict was missed: {form}")
    if re.search(module.POSIX_Y_CONFLICT_PATTERN, "function yazi {", re.MULTILINE):
        raise AssertionError("unrelated POSIX function was treated as y")


def check_yazi_environment_broadcast():
    module = load_module(
        "workspace_personal_yazi_environment_broadcast",
        WORKSPACE_ROOT / "applications" / "yazi" / "install_yazi.py",
    )
    original_is_windows = module.IS_WINDOWS
    original_win_dll = getattr(ctypes, "WinDLL", None)
    calls = []

    class FakeSendMessageTimeout:
        def __call__(self, *args):
            calls.append(args)
            return 1

    class FakeUser32:
        SendMessageTimeoutW = FakeSendMessageTimeout()

    def fake_windll(name, use_last_error=False):
        if name != "user32" or not use_last_error:
            raise AssertionError("Yazi requested an unexpected user32 load")
        return FakeUser32()

    try:
        module.IS_WINDOWS = True
        ctypes.WinDLL = fake_windll
        module.broadcast_windows_environment_change()
    finally:
        module.IS_WINDOWS = original_is_windows
        if original_win_dll is None:
            delattr(ctypes, "WinDLL")
        else:
            ctypes.WinDLL = original_win_dll

    if len(calls) != 1:
        raise AssertionError("Yazi did not broadcast exactly one environment change")
    call = calls[0]
    scalar = lambda value: value.value if hasattr(value, "value") else int(value)
    if (scalar(call[0]), scalar(call[1]), scalar(call[4]), scalar(call[5])) != (
        0xFFFF, 0x001A, 0x0002, 5000
    ):
        raise AssertionError("Yazi used the wrong WM_SETTINGCHANGE broadcast parameters")

    original_environment = os.environ.get("YAZI_FILE_ONE")
    original_broadcast = module.broadcast_windows_environment_change
    registry_calls = []
    fake_key = object()
    fake_winreg = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        KEY_SET_VALUE=2,
        REG_EXPAND_SZ=3,
        OpenKey=lambda *_args: fake_key,
        SetValueEx=lambda _key, name, _reserved, _kind, value:
            registry_calls.append((name, value)),
        CloseKey=lambda _key: None,
    )
    previous_winreg = sys.modules.get("winreg")
    try:
        sys.modules["winreg"] = fake_winreg
        module.broadcast_windows_environment_change = lambda: calls.append(("env",))
        module.set_windows_user_env("YAZI_FILE_ONE", r"C:\Git\usr\bin\file.exe")
    finally:
        module.broadcast_windows_environment_change = original_broadcast
        if previous_winreg is None:
            sys.modules.pop("winreg", None)
        else:
            sys.modules["winreg"] = previous_winreg
        if original_environment is None:
            os.environ.pop("YAZI_FILE_ONE", None)
        else:
            os.environ["YAZI_FILE_ONE"] = original_environment

    if registry_calls != [("YAZI_FILE_ONE", r"C:\Git\usr\bin\file.exe")]:
        raise AssertionError("Yazi did not write the requested user environment value")
    if ("env",) not in calls:
        raise AssertionError("Yazi did not invoke the environment broadcast after writing")


def check_yazi_toml_escaping():
    module = load_module(
        "workspace_personal_yazi_toml",
        WORKSPACE_ROOT / "applications" / "yazi" / "install_yazi.py",
    )
    config_dir = SCRATCH_ROOT / "yazi-toml"
    keys = ('g"', "v\\")
    target = r'C:\quoted "workspace"\nested'
    description = 'Description "quoted"\t'
    with contextlib.redirect_stdout(io.StringIO()):
        module.configure_keymap_shortcut(keys, target, description, config_dir)
    keymap_path = config_dir / "keymap.toml"
    content = keymap_path.read_bytes().decode("utf-8")
    if module.toml_basic_string("cd " + target) not in content:
        raise AssertionError("Yazi target path was not TOML-escaped")
    if module.toml_basic_string(description) not in content:
        raise AssertionError("Yazi description was not TOML-escaped")
    for key in keys:
        if module.toml_basic_string(key) not in content:
            raise AssertionError("Yazi key was not TOML-escaped")

    try:
        import tomllib
    except ImportError:
        tomllib = None
    if tomllib is not None:
        parsed = tomllib.loads(content)
        entry = parsed["mgr"]["prepend_keymap"][0]
        if entry["on"] != list(keys):
            raise AssertionError("escaped Yazi keys did not round-trip through TOML")
        if entry["run"] != "cd " + target:
            raise AssertionError("escaped Yazi run command did not preserve its path")
        if entry["desc"] != description:
            raise AssertionError("escaped Yazi description did not round-trip through TOML")

    before = keymap_path.read_bytes()
    with contextlib.redirect_stdout(io.StringIO()):
        module.configure_keymap_shortcut(keys, target, description, config_dir)
    if keymap_path.read_bytes() != before:
        raise AssertionError("escaped Yazi keymap binding was not idempotent")


def _assert_line_ending_style(data, style, label):
    text = data.decode("utf-8")
    if style == "\r\n":
        if "\r\n" not in text or text.replace("\r\n", "").count("\r") \
                or text.replace("\r\n", "").count("\n"):
            raise AssertionError(f"{label} changed its CRLF style")
    elif style == "\r":
        if "\n" in text:
            raise AssertionError(f"{label} changed its CR style")
    elif "\r" in text:
        raise AssertionError(f"{label} changed its LF style")


def check_line_ending_preservation():
    wezterm = load_module(
        "workspace_personal_wezterm_line_endings",
        WORKSPACE_ROOT / "applications" / "wezterm" / "install_wezterm.py",
    )
    yazi = load_module(
        "workspace_personal_yazi_line_endings",
        WORKSPACE_ROOT / "applications" / "yazi" / "install_yazi.py",
    )
    for index, newline in enumerate(("\n", "\r\n", "\r")):
        wezterm_path = SCRATCH_ROOT / f"wezterm-line-endings-{index}.lua"
        wezterm_prefix = (
            "local config = wezterm.config_builder()"
            + newline
            + "-- user setting"
            + newline
        )
        wezterm_path.write_bytes((wezterm_prefix + "return config" + newline).encode("utf-8"))
        wezterm.upsert_marked_block(
            wezterm_path,
            "shell-approach",
            ["config.default_prog = { 'pwsh.exe' }"],
        )
        wezterm_data = wezterm_path.read_bytes()
        if not wezterm_data.startswith(wezterm_prefix.encode("utf-8")):
            raise AssertionError("WezTerm user content was rewritten outside its block")
        _assert_line_ending_style(wezterm_data, newline, "WezTerm config")

        keymap_dir = SCRATCH_ROOT / f"yazi-keymap-line-endings-{index}"
        keymap_path = keymap_dir / "keymap.toml"
        keymap_prefix = "[mgr]" + newline + "show_hidden = true" + newline
        keymap_path.parent.mkdir(parents=True, exist_ok=True)
        keymap_path.write_bytes(keymap_prefix.encode("utf-8"))
        with contextlib.redirect_stdout(io.StringIO()):
            yazi.configure_keymap_shortcut(
                ["g", "v"], r"C:\workspace", "Go to workspace", keymap_dir
            )
        keymap_data = keymap_path.read_bytes()
        if not keymap_data.startswith(keymap_prefix.encode("utf-8")):
            raise AssertionError("Yazi keymap content was rewritten outside its block")
        _assert_line_ending_style(keymap_data, newline, "Yazi keymap")

        yazi_toml = SCRATCH_ROOT / f"yazi-line-endings-{index}.toml"
        yazi_prefix = "[manager]" + newline + "show_hidden = true" + newline
        yazi_toml.write_bytes(yazi_prefix.encode("utf-8"))
        yazi.upsert_marker_block(
            yazi_toml,
            "markdown-preview",
            'url = "*.md"',
        )
        yazi_data = yazi_toml.read_bytes()
        if not yazi_data.startswith(yazi_prefix.encode("utf-8")):
            raise AssertionError("Yazi TOML content was rewritten outside its block")
        _assert_line_ending_style(yazi_data, newline, "Yazi TOML")


def check_existing_executable_discovery():
    wezterm = load_module(
        "workspace_personal_wezterm_existing",
        WORKSPACE_ROOT / "applications" / "wezterm" / "install_wezterm.py",
    )
    yazi = load_module(
        "workspace_personal_yazi_existing",
        WORKSPACE_ROOT / "applications" / "yazi" / "install_yazi.py",
    )
    original_which = wezterm.shutil.which
    try:
        def fake_which(name):
            if name == "wezterm":
                return r"C:\Tools\wezterm.exe"
            if name == "yazi":
                return r"C:\Tools\yazi.exe"
            return None

        wezterm.shutil.which = fake_which
        with contextlib.redirect_stdout(io.StringIO()):
            if not wezterm.install_wezterm():
                raise AssertionError("existing WezTerm executable was not accepted")
            if not yazi.install_yazi():
                raise AssertionError("existing Yazi executable was not accepted")
    finally:
        wezterm.shutil.which = original_which


def check_powershell_post_install_discovery():
    module = load_module(
        "workspace_personal_powershell",
        WORKSPACE_ROOT / "applications" / "powershell" / "install_powershell.py",
    )
    install_root = SCRATCH_ROOT / "pwsh-install"
    executable = install_root / "PowerShell" / "7" / "pwsh.exe"
    original_which = module.shutil.which
    original_install_package = module.install_package
    original_path = os.environ.get("PATH")
    original_environment = {
        key: os.environ.get(key)
        for key in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA")
    }
    calls = []
    try:
        os.environ["ProgramFiles"] = str(install_root)
        os.environ["ProgramW6432"] = str(install_root)
        os.environ["LOCALAPPDATA"] = str(install_root / "LocalAppData")

        def fake_which(name):
            return None

        def fake_install_package(*args, **kwargs):
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b"")
            return True

        module.shutil.which = fake_which
        module.install_package = fake_install_package
        if not module.install_pwsh():
            raise AssertionError("successful pwsh install was not discovered")
        if str(executable.parent) not in os.environ["PATH"].split(os.pathsep):
            raise AssertionError("fresh pwsh install was not added to PATH")

        profile_paths = [
            r"V:\profiles\pwsh café-all-hosts.ps1",
            r"V:\profiles\pwsh-current.ps1",
            r"V:\profiles\all-users-all-hosts.ps1",
            r"V:\profiles\all-users-current-host.ps1",
        ]
        encoded_profiles = base64.b64encode(
            "\n".join(profile_paths).encode("utf-8")
        )

        def fake_run(command, **kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=0, stdout=encoded_profiles)

        original_run = module.subprocess.run
        module.subprocess.run = fake_run
        try:
            resolved_paths = module.profile_paths("pwsh", str(executable))
            targets, scan_paths = module.resolve_profile_paths(
                pwsh_executable=str(executable)
            )
        finally:
            module.subprocess.run = original_run
        if resolved_paths != [Path(path) for path in profile_paths]:
            raise AssertionError("profile discovery did not decode all four paths")
        if targets != [Path(profile_paths[0])] or scan_paths != [
            Path(path) for path in profile_paths
        ]:
            raise AssertionError("PowerShell targets and scan paths were not separated")
        if not calls or calls[0][0] != str(executable):
            raise AssertionError("profile discovery did not invoke the installed pwsh")
        call_kwargs = {}
        original_run = module.subprocess.run

        def inspect_run(command, **kwargs):
            call_kwargs.update(kwargs)
            return SimpleNamespace(returncode=0, stdout=encoded_profiles)

        module.subprocess.run = inspect_run
        try:
            module.profile_all_hosts_path("pwsh", str(executable))
        finally:
            module.subprocess.run = original_run
        if call_kwargs.get("text") is not False:
            raise AssertionError("profile discovery did not request byte output")

        def failed_run(command, **_kwargs):
            return SimpleNamespace(returncode=1, stdout=encoded_profiles)

        original_run = module.subprocess.run
        module.subprocess.run = failed_run
        try:
            try:
                module.profile_all_hosts_path("pwsh", str(executable))
            except module.ProfileDiscoveryError:
                pass
            else:
                raise AssertionError("nonzero PowerShell discovery was accepted")
        finally:
            module.subprocess.run = original_run

        malformed_profiles = base64.b64encode(
            "\n".join(profile_paths[:3]).encode("utf-8")
        )
        module.subprocess.run = lambda _command, **_kwargs: SimpleNamespace(
            returncode=0, stdout=malformed_profiles
        )
        try:
            try:
                module.profile_all_hosts_path("pwsh", str(executable))
            except module.ProfileDiscoveryError:
                pass
            else:
                raise AssertionError("malformed PowerShell discovery was accepted")
        finally:
            module.subprocess.run = original_run

        module.subprocess.run = lambda _command, **_kwargs: (
            (_ for _ in ()).throw(
                subprocess.TimeoutExpired(str(executable), 30)
            )
        )
        try:
            try:
                module.profile_all_hosts_path("pwsh", str(executable))
            except module.ProfileDiscoveryError:
                pass
            else:
                raise AssertionError("timed-out PowerShell discovery was accepted")
        finally:
            module.subprocess.run = original_run
    finally:
        module.shutil.which = original_which
        module.install_package = original_install_package
        if original_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = original_path
        for key, value in original_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def check_yazi_profile_discovery():
    module = load_module(
        "workspace_personal_yazi_profile_discovery",
        WORKSPACE_ROOT / "applications" / "yazi" / "install_yazi.py",
    )
    profile_sets = {
        "powershell.exe": [
            r"V:\profiles\powershell café-all-hosts.ps1",
            r"V:\profiles\powershell-current.ps1",
            r"V:\profiles\powershell-all-users-all-hosts.ps1",
            r"V:\profiles\powershell-all-users-current-host.ps1",
        ],
        "pwsh.exe": [
            r"V:\profiles\pwsh café-all-hosts.ps1",
            r"V:\profiles\pwsh-current.ps1",
            r"V:\profiles\pwsh-all-users-all-hosts.ps1",
            r"V:\profiles\pwsh-all-users-current-host.ps1",
        ],
    }
    encoded_profiles = {
        executable: base64.b64encode("\n".join(paths).encode("utf-8"))
        for executable, paths in profile_sets.items()
    }
    original_which = module.shutil.which
    original_run = module.subprocess.run
    calls = []
    try:
        module.shutil.which = lambda name: f"{name}.exe"

        def fake_run(command, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                returncode=0,
                stdout=encoded_profiles[Path(command[0]).name],
            )

        module.subprocess.run = fake_run
        targets = module.powershell_profile_paths()
        scan_paths = module.powershell_profile_scan_paths()
        expected_targets = [
            Path(profile_sets["powershell.exe"][0]),
            Path(profile_sets["pwsh.exe"][0]),
        ]
        expected_scan_paths = [
            Path(path)
            for executable in ("powershell.exe", "pwsh.exe")
            for path in profile_sets[executable]
        ]
        if targets != expected_targets:
            raise AssertionError("Yazi did not retain only CurrentUserAllHosts targets")
        if scan_paths != expected_scan_paths:
            raise AssertionError("Yazi did not scan all four PowerShell profile paths")
        if any(call.get("text") is not False for call in calls):
            raise AssertionError("Yazi profile discovery did not request byte output")

        module.subprocess.run = lambda _command, **_kwargs: SimpleNamespace(
            returncode=1, stdout=encoded_profiles["pwsh.exe"]
        )
        try:
            module.powershell_profile_scan_paths()
        except module.ProfileDiscoveryError:
            pass
        else:
            raise AssertionError("Yazi accepted output from a failed interpreter")

        module.subprocess.run = lambda _command, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=base64.b64encode(
                "\n".join(profile_sets["pwsh.exe"][:3]).encode("utf-8")
            ),
        )
        try:
            try:
                module.powershell_profile_scan_paths()
            except module.ProfileDiscoveryError:
                pass
            else:
                raise AssertionError("Yazi accepted malformed profile discovery")
        finally:
            module.subprocess.run = original_run
    finally:
        module.shutil.which = original_which
        module.subprocess.run = original_run


def check_wezterm_wsl_output_decoding():
    module = load_module(
        "workspace_personal_wezterm_wsl_output",
        WORKSPACE_ROOT / "applications" / "wezterm" / "install_wezterm.py",
    )
    original_run = module.subprocess.run
    try:
        for raw in (
            "Dév\r\nUbuntu\r\n".encode("utf-16-le"),
            codecs.BOM_UTF16_LE + "Dév\r\nUbuntu\r\n".encode("utf-16-le"),
            "Dév\nUbuntu\n".encode("utf-8"),
        ):
            module.subprocess.run = lambda _command, raw=raw, **_kwargs: (
                SimpleNamespace(returncode=0, stdout=raw)
            )
            if module.list_wsl_distros() != ["Dév", "Ubuntu"]:
                raise AssertionError("WezTerm did not preserve non-ASCII WSL names")
    finally:
        module.subprocess.run = original_run


def check_wezterm_post_install_discovery():
    module = load_module(
        "workspace_personal_wezterm_pwsh",
        WORKSPACE_ROOT / "applications" / "wezterm" / "install_wezterm.py",
    )
    install_root = SCRATCH_ROOT / "wezterm-pwsh-install"
    executable = install_root / "PowerShell" / "7" / "pwsh.exe"
    original_which = module.shutil.which
    original_install_package = module.install_package
    original_path = os.environ.get("PATH")
    original_is_windows = module.IS_WINDOWS
    original_environment = {
        key: os.environ.get(key)
        for key in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA")
    }
    try:
        module.IS_WINDOWS = True
        os.environ["ProgramFiles"] = str(install_root)
        os.environ["ProgramW6432"] = str(install_root)
        os.environ["LOCALAPPDATA"] = str(install_root / "LocalAppData")

        def fake_which(name):
            if name in ("pwsh", "pwsh.exe"):
                return None
            return original_which(name)

        def fake_install_package(*args, **kwargs):
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b"")
            return True

        module.shutil.which = fake_which
        module.install_package = fake_install_package
        if not module.install_pwsh():
            raise AssertionError("successful WezTerm pwsh install was not discovered")
        if str(executable.parent) not in os.environ["PATH"].split(os.pathsep):
            raise AssertionError("WezTerm did not refresh PATH after pwsh install")
        if module.find_pwsh_executable() != str(executable):
            raise AssertionError("WezTerm lost the explicit post-install pwsh path")
        menu = module.shell_switching_lines(
            [],
            pwsh_available=bool(module.find_pwsh_executable()),
        )
        if not any("pwsh.exe" in line for line in menu):
            raise AssertionError("shell switching still advertised Windows PowerShell")
    finally:
        module.shutil.which = original_which
        module.install_package = original_install_package
        module.IS_WINDOWS = original_is_windows
        if original_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = original_path
        for key, value in original_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def check_wsl_hostname_merge():
    module = load_module(
        "workspace_personal_wsl_hostname",
        WORKSPACE_ROOT / "applications" / "wsl" / "install_wsl.py",
    )
    original_read = module.read_wsl_conf
    original_write = module.write_wsl_conf
    original_run = module.run

    try:
        writes = []
        module.read_wsl_conf = lambda _instance: (
            "[boot]\n"
            "systemd = true\n"
            "[network]\n"
            "generateResolvConf = false\n"
            "generateHosts = true\n"
            "[interop]\n"
            "hostname = unrelated\n"
        )
        module.write_wsl_conf = lambda _instance, content: writes.append(content)
        module.run = lambda *_args, **_kwargs: SimpleNamespace(returncode=0)
        if not module.configure_wsl_hostname("personal", "personal-wsl"):
            raise AssertionError("WSL hostname merge unexpectedly failed")
        if len(writes) != 1:
            raise AssertionError("WSL hostname merge did not write exactly once")
        merged = writes[0]
        if len(re.findall(r"(?im)^\s*\[\s*network\s*\]\s*$", merged)) != 1:
            raise AssertionError("WSL hostname merge created a duplicate [network] section")
        if "generateResolvConf = false" not in merged or \
                "generateHosts = true" not in merged:
            raise AssertionError("WSL hostname merge lost an existing network setting")
        if "hostname = personal-wsl" not in merged:
            raise AssertionError("WSL hostname was not added to the existing network section")

        writes.clear()
        module.read_wsl_conf = lambda _instance: merged
        if not module.configure_wsl_hostname("personal", "personal-wsl"):
            raise AssertionError("WSL hostname rerun unexpectedly failed")
        if writes:
            raise AssertionError("WSL hostname merge was not idempotent")

        writes.clear()
        module.read_wsl_conf = lambda _instance: (
            "[network]\n"
            "generateResolvConf = false\n"
            "hostname = other-host\n"
        )
        if not module.configure_wsl_hostname("personal", "personal-wsl"):
            raise AssertionError("conflicting manual hostname was treated as a hard failure")
        if writes:
            raise AssertionError("conflicting manual hostname was overwritten")

        writes.clear()
        module.read_wsl_conf = lambda _instance: (
            "[network]\n"
            "generateResolvConf = false\n"
            "[interop]\n"
            "hostname = other-section\n"
        )
        if not module.configure_wsl_hostname("personal", "personal-wsl"):
            raise AssertionError("hostname in another WSL section caused a conflict")
        if len(writes) != 1 or "hostname = personal-wsl" not in writes[0]:
            raise AssertionError("WSL hostname was not merged into [network]")
    finally:
        module.read_wsl_conf = original_read
        module.write_wsl_conf = original_write
        module.run = original_run


def check_wsl_conf_read_safety():
    module = load_module(
        "workspace_personal_wsl_conf_read_safety",
        WORKSPACE_ROOT / "applications" / "wsl" / "install_wsl.py",
    )
    original_subprocess_run = module.subprocess.run
    original_write = module.write_wsl_conf
    writes = []
    try:
        calls = []

        def missing_run(command, **_kwargs):
            calls.append(command)
            if command[-2:] == ["cat", "/etc/wsl.conf"]:
                return SimpleNamespace(returncode=1, stdout=b"", stderr=b"missing")
            if command[-4:] == ["test", "!", "-e", "/etc/wsl.conf"]:
                return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
            raise AssertionError(f"unexpected missing-file command: {command}")

        module.subprocess.run = missing_run
        if module.read_wsl_conf("personal") != "":
            raise AssertionError("confirmed-missing /etc/wsl.conf was not treated as empty")
        if len(calls) != 2:
            raise AssertionError("missing-file confirmation did not run after cat failed")

        def failed_read_run(command, **_kwargs):
            if command[-2:] == ["cat", "/etc/wsl.conf"]:
                return SimpleNamespace(returncode=1, stdout=b"", stderr=b"permission denied")
            if command[-4:] == ["test", "!", "-e", "/etc/wsl.conf"]:
                return SimpleNamespace(returncode=1, stdout=b"", stderr=b"")
            raise AssertionError(f"unexpected failed-read command: {command}")

        module.subprocess.run = failed_read_run
        module.write_wsl_conf = lambda _instance, content: writes.append(content)
        try:
            module.configure_wsl_hostname("personal", "personal-wsl")
        except module.WslConfReadError:
            pass
        else:
            raise AssertionError("failed /etc/wsl.conf read was treated as empty")
        if writes:
            raise AssertionError("failed /etc/wsl.conf read allowed a destructive write")

        module.subprocess.run = lambda command, **_kwargs: (
            SimpleNamespace(returncode=0, stdout=b"\xff", stderr=b"")
            if command[-2:] == ["cat", "/etc/wsl.conf"]
            else (_ for _ in ()).throw(
                AssertionError(f"unexpected invalid-UTF8 command: {command}")
            )
        )
        try:
            module.read_wsl_conf("personal")
        except module.WslConfReadError:
            pass
        else:
            raise AssertionError("invalid UTF-8 /etc/wsl.conf was accepted")
    finally:
        module.subprocess.run = original_subprocess_run
        module.write_wsl_conf = original_write


def check_wsl_name_and_path_safety():
    module = load_module(
        "workspace_personal_wsl_name_safety",
        WORKSPACE_ROOT / "applications" / "wsl" / "install_wsl.py",
    )
    environment_keys = (
        "LOCALAPPDATA",
        "WORKSPACE_PERSONAL_WSL_EXPORT_DIR",
        "WORKSPACE_PERSONAL_WSL_LOCK_DIR",
        "WORKSPACE_PERSONAL_WSL_USER_STATE_DIR",
    )
    original_environment = {key: os.environ.get(key) for key in environment_keys}
    try:
        roots = {
            "LOCALAPPDATA": SCRATCH_ROOT / "local-app-data-safe",
            "WORKSPACE_PERSONAL_WSL_EXPORT_DIR": SCRATCH_ROOT / "exports-safe",
            "WORKSPACE_PERSONAL_WSL_LOCK_DIR": SCRATCH_ROOT / "locks-safe",
            "WORKSPACE_PERSONAL_WSL_USER_STATE_DIR": SCRATCH_ROOT / "state-safe",
        }
        for key, value in roots.items():
            os.environ[key] = str(value)

        invalid_names = (".", "..", "../escape", r"..\escape", r"C:\escape",
                         "/tmp/escape", "bad?name", "name ", "CON")
        for name in invalid_names:
            try:
                module.validate_wsl_name(name)
            except ValueError:
                pass
            else:
                raise AssertionError(f"unsafe WSL name was accepted: {name!r}")

        lock_a = module._wsl_target_lock_path("Dév")
        lock_b = module._wsl_target_lock_path("Dèv")
        state_a = module._expected_wsl_user_path("Dév")
        state_b = module._expected_wsl_user_path("Dèv")
        if lock_a == lock_b or state_a == state_b:
            raise AssertionError("distinct WSL names collided in state or lock storage")
        if lock_a.parent != roots["WORKSPACE_PERSONAL_WSL_LOCK_DIR"].resolve():
            raise AssertionError("WSL lock path escaped its configured root")
        if state_a.parent != roots["WORKSPACE_PERSONAL_WSL_USER_STATE_DIR"].resolve():
            raise AssertionError("WSL state path escaped its configured root")

        export_path = module.default_export_path("Ubuntu", "Dév")
        if export_path.parent != roots["WORKSPACE_PERSONAL_WSL_EXPORT_DIR"].resolve():
            raise AssertionError("WSL export path escaped its configured root")
        try:
            module.default_export_path("Ubuntu", "../escape")
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe WSL export name was accepted")
    finally:
        for key, value in original_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def check_wsl_default_user_handling():
    module = load_module(
        "workspace_personal_wsl_default_user",
        WORKSPACE_ROOT / "applications" / "wsl" / "install_wsl.py",
    )
    original_local_app_data = os.environ.get("LOCALAPPDATA")
    original_run = module.run
    original_ask = module.ask
    original_read = module.read_wsl_conf
    original_write = module.write_wsl_conf
    original_list_distro_info = module.list_wsl_distro_info
    export_path = SCRATCH_ROOT / "wsl-default-user.tar"
    calls = []
    writes = []
    try:
        os.environ["LOCALAPPDATA"] = str(SCRATCH_ROOT / "local-app-data-user")
        module.list_wsl_distro_info = lambda: [
            {"name": "Ubuntu", "state": "Stopped", "version": 2}
        ]
        module.ask = lambda _prompt: "n"
        module.read_wsl_conf = lambda _instance: (
            "[boot]\r\n"
            "systemd = true\r\n"
            "[user]\r\n"
            "locale = C.UTF-8\r\n"
            "default = old-user # preserve this comment\r\n"
        )
        module.write_wsl_conf = lambda _instance, content: writes.append(content)

        def fake_run(command, **_kwargs):
            calls.append(command)
            if command == ["wsl", "-d", "Ubuntu", "--", "id", "-un"]:
                return SimpleNamespace(returncode=0, stdout=b"alice\n")
            if command == ["wsl", "-d", "Ubuntu", "--", "id", "-u"]:
                return SimpleNamespace(returncode=0, stdout=b"1000\n")
            if command[1] == "--export":
                export_path.write_bytes(b"archive")
                return SimpleNamespace(returncode=0)
            if command[1] == "--import":
                return SimpleNamespace(returncode=0)
            if command[1] == "--terminate":
                return SimpleNamespace(returncode=0)
            if command == ["wsl", "-d", "personal", "--", "id", "-un"]:
                return SimpleNamespace(returncode=0, stdout=b"alice\n")
            if command == ["wsl", "-d", "personal", "--", "id", "-u"]:
                return SimpleNamespace(returncode=0, stdout=b"1000\n")
            raise AssertionError(f"unexpected WSL command: {command}")

        module.run = fake_run
        if module.rename_distro("Ubuntu", "personal", export_path) is not True:
            raise AssertionError("WSL rename with a valid default user failed")
        if len(writes) != 1 or "default = alice # preserve this comment" not in writes[0]:
            raise AssertionError("WSL default user was not merged into existing settings")
        if "systemd = true" not in writes[0] or "locale = C.UTF-8" not in writes[0]:
            raise AssertionError("WSL user/config settings were not preserved")
        if "\r\n" not in writes[0] or "\n" in writes[0].replace("\r\n", ""):
            raise AssertionError("WSL default-user merge changed CRLF line endings")
        expected_order = [
            ["wsl", "-d", "Ubuntu", "--", "id", "-un"],
            ["wsl", "-d", "Ubuntu", "--", "id", "-u"],
            ["wsl", "--export", "Ubuntu", str(export_path)],
            ["wsl", "--import", "personal",
             str(Path(os.environ["LOCALAPPDATA"]) / "WSL" / "personal"),
             str(export_path), "--version", "2"],
            ["wsl", "--terminate", "personal"],
            ["wsl", "-d", "personal", "--", "id", "-un"],
            ["wsl", "-d", "personal", "--", "id", "-u"],
        ]
        if calls != expected_order:
            raise AssertionError(f"WSL user verification order changed: {calls}")

        calls.clear()
        writes.clear()
        module.run = lambda command, **_kwargs: (
            SimpleNamespace(returncode=0, stdout=b"root\n")
            if command == ["wsl", "-d", "Ubuntu", "--", "id", "-un"]
            else SimpleNamespace(returncode=0, stdout=b"0\n")
            if command == ["wsl", "-d", "Ubuntu", "--", "id", "-u"]
            else (_ for _ in ()).throw(AssertionError(f"unexpected root-path command: {command}"))
        )
        if module.rename_distro("Ubuntu", "personal", SCRATCH_ROOT / "wsl-root.tar") is not False:
            raise AssertionError("root-default WSL distro was not refused")
        if calls or writes:
            raise AssertionError("root-default refusal performed setup work")
    finally:
        module.run = original_run
        module.ask = original_ask
        module.read_wsl_conf = original_read
        module.write_wsl_conf = original_write
        module.list_wsl_distro_info = original_list_distro_info
        if original_local_app_data is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = original_local_app_data


def check_wsl_existing_target_user():
    module = load_module(
        "workspace_personal_wsl_existing_target_user",
        WORKSPACE_ROOT / "applications" / "wsl" / "install_wsl.py",
    )
    original_is_windows = module.IS_WINDOWS
    original_which = module.shutil.which
    original_list_distro_info = module.list_wsl_distro_info
    original_resolve_user = module.resolve_effective_wsl_user
    original_resolve_uid = module.resolve_effective_wsl_uid
    original_local_app_data = os.environ.get("LOCALAPPDATA")
    try:
        os.environ["LOCALAPPDATA"] = str(
            SCRATCH_ROOT / "wsl-expected-target-user"
        )
        module.IS_WINDOWS = True
        module.shutil.which = lambda name: "wsl.exe" if name == "wsl" else None
        module.list_wsl_distro_info = lambda: [
            {"name": "personal", "state": "Stopped", "version": 2}
        ]
        module.resolve_effective_wsl_user = lambda _instance: "root"
        module.resolve_effective_wsl_uid = lambda _instance: 0
        if module.configure_wsl("Ubuntu", "personal") is not False:
            raise AssertionError("existing root-default WSL target was accepted")

        module.resolve_effective_wsl_user = lambda _instance: "alice"
        module.resolve_effective_wsl_uid = lambda _instance: 1000
        if module.configure_wsl("Ubuntu", "personal") is not True:
            raise AssertionError("existing non-root WSL target was rejected")

        module.resolve_effective_wsl_user = lambda _instance: "bob"
        module.resolve_effective_wsl_uid = lambda _instance: 1000
        if module.ensure_wsl2("personal", expected_user="alice") is not False:
            raise AssertionError("WSL target user mismatch was accepted")
    finally:
        module.IS_WINDOWS = original_is_windows
        module.shutil.which = original_which
        module.list_wsl_distro_info = original_list_distro_info
        module.resolve_effective_wsl_user = original_resolve_user
        module.resolve_effective_wsl_uid = original_resolve_uid
        if original_local_app_data is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = original_local_app_data


def check_wsl_cleanup_failure_propagation():
    module = load_module(
        "workspace_personal_wsl",
        WORKSPACE_ROOT / "applications" / "wsl" / "install_wsl.py",
    )
    local_app_data = SCRATCH_ROOT / "local-app-data"
    export_path = SCRATCH_ROOT / "wsl-export.tar"
    original_local_app_data = os.environ.get("LOCALAPPDATA")
    original_run = module.run
    original_ask = module.ask
    original_unlink = Path.unlink
    original_which = module.shutil.which
    original_list_distro_info = module.list_wsl_distro_info
    original_rename_distro = module.rename_distro
    original_ensure_wsl2 = module.ensure_wsl2
    original_resolve_user = module.resolve_effective_wsl_user
    original_resolve_uid = module.resolve_effective_wsl_uid
    original_establish_user = module.establish_imported_default_user
    try:
        os.environ["LOCALAPPDATA"] = str(local_app_data)
        module.list_wsl_distro_info = lambda: [
            {"name": "Ubuntu", "state": "Stopped", "version": 2}
        ]
        module.resolve_effective_wsl_user = lambda _instance: "alice"
        module.resolve_effective_wsl_uid = lambda _instance: 1000
        module.establish_imported_default_user = lambda *_args: True

        def fake_run(command, **kwargs):
            if command[1] == "--export":
                export_path.write_bytes(b"archive")
            return SimpleNamespace(returncode=0)

        module.run = fake_run
        module.ask = lambda _prompt: "n"

        def refuse_archive_cleanup(path, *args, **kwargs):
            if path == export_path:
                raise OSError("archive is locked")
            return original_unlink(path, *args, **kwargs)

        Path.unlink = refuse_archive_cleanup
        if module.rename_distro("Ubuntu", "personal", export_path) is not False:
            raise AssertionError("archive cleanup failure was reported as success")

        Path.unlink = original_unlink
        try:
            export_path.unlink()
        except FileNotFoundError:
            pass
        module.ask = lambda _prompt: "y"

        def failed_unregister(command, **kwargs):
            if command[1] == "--export":
                export_path.write_bytes(b"archive")
            if command[1] == "--unregister":
                return SimpleNamespace(returncode=1)
            return SimpleNamespace(returncode=0)

        module.run = failed_unregister
        if module.rename_distro("Ubuntu", "personal", export_path) is not False:
            raise AssertionError("unregister failure was reported as success")

        try:
            export_path.unlink()
        except FileNotFoundError:
            pass
        module.establish_imported_default_user = lambda *_args: False
        module.ask = lambda _prompt: "n"
        partial_cleanup_calls = []

        def partial_import_run(command, **kwargs):
            if command[1] == "--export":
                export_path.write_bytes(b"archive")
            if command[1] == "--unregister":
                partial_cleanup_calls.append(command)
            return SimpleNamespace(returncode=0)

        module.run = partial_import_run
        if module.rename_distro("Ubuntu", "personal", export_path) is not False:
            raise AssertionError("failed imported-user repair was reported as success")
        if partial_cleanup_calls != [["wsl", "--unregister", "personal"]]:
            raise AssertionError("failed imported-user repair did not unregister the partial target")
        try:
            export_path.unlink()
        except FileNotFoundError:
            pass

        module.shutil.which = lambda name: "wsl.exe" if name == "wsl" else None
        module.list_wsl_distro_info = lambda: [{"name": "Ubuntu", "version": 2}]
        module.rename_distro = lambda *_args: False
        ensure_called = False

        def unexpected_ensure(_instance_name):
            nonlocal ensure_called
            ensure_called = True
            return True

        module.ensure_wsl2 = unexpected_ensure
        answers = iter(("y", "y"))
        module.ask = lambda _prompt: next(answers)
        if module.configure_wsl("Ubuntu", "personal", export_path) is not False:
            raise AssertionError("configure_wsl masked a failed rename")
        if ensure_called:
            raise AssertionError("configure_wsl continued after a failed rename")
    finally:
        Path.unlink = original_unlink
        module.run = original_run
        module.ask = original_ask
        module.shutil.which = original_which
        module.list_wsl_distro_info = original_list_distro_info
        module.rename_distro = original_rename_distro
        module.ensure_wsl2 = original_ensure_wsl2
        module.resolve_effective_wsl_user = original_resolve_user
        module.resolve_effective_wsl_uid = original_resolve_uid
        module.establish_imported_default_user = original_establish_user
        if original_local_app_data is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = original_local_app_data


def powershell_executable():
    requested = os.environ.get("WORKSPACE_PERSONAL_CHECK_POWERSHELL")
    if requested:
        return requested
    for name in ("pwsh", "powershell"):
        executable = shutil.which(name)
        if executable:
            return executable
    raise RuntimeError("pwsh or Windows PowerShell is required for the focused checks")


def check_yazi_utf16_profile():
    module = load_module(
        "workspace_personal_yazi",
        WORKSPACE_ROOT / "applications" / "yazi" / "install_yazi.py",
    )
    profile = SCRATCH_ROOT / "profile.ps1"
    profile.write_bytes("# existing profile\r\n".encode("utf-16"))
    original_is_windows = module.IS_WINDOWS
    try:
        module.IS_WINDOWS = True
        module.configure_cd_on_exit([profile])
    finally:
        module.IS_WINDOWS = original_is_windows

    data = profile.read_bytes()
    if not (data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE)):
        raise AssertionError("Yazi profile write lost the UTF-16 BOM")
    text = data.decode("utf-16")
    if module.MARKER_BEGIN.format(name="y function") not in text:
        raise AssertionError("Yazi marker was not added to the UTF-16 profile")
    if "# existing profile" not in text:
        raise AssertionError("existing UTF-16 profile content was not preserved")


def check_managed_marker_validation():
    wezterm = load_module(
        "workspace_personal_wezterm_marker_validation",
        WORKSPACE_ROOT / "applications" / "wezterm" / "install_wezterm.py",
    )
    wezterm_path = SCRATCH_ROOT / "wezterm-duplicate-markers.lua"
    wezterm_begin = wezterm.MARKER_BEGIN.format(name="shell-approach")
    wezterm_end = wezterm.MARKER_END.format(name="shell-approach")
    wezterm_path.write_text(
        "local wezterm = require 'wezterm'\n"
        "local config = wezterm.config_builder()\n"
        f"{wezterm_begin}\n"
        "config.default_prog = { 'pwsh.exe' }\n"
        f"{wezterm_end}\n"
        f"{wezterm_begin}\n"
        "config.default_prog = { 'cmd.exe' }\n"
        f"{wezterm_end}\n"
        "return config\n",
        encoding="utf-8",
    )
    before = wezterm_path.read_bytes()
    try:
        wezterm.upsert_marked_block(
            wezterm_path,
            "shell-approach",
            ["config.default_prog = { 'pwsh.exe' }"],
        )
    except wezterm.ManagedMarkerError:
        pass
    else:
        raise AssertionError("WezTerm duplicate managed markers were accepted")
    if wezterm_path.read_bytes() != before:
        raise AssertionError("WezTerm changed a config with duplicate markers")

    wsl = load_module(
        "workspace_personal_wsl_marker_validation",
        WORKSPACE_ROOT / "applications" / "wsl" / "install_wsl.py",
    )
    wsl_begin = wsl.WSL_CONF_MARKER_BEGIN.format(name="hostname")
    wsl_other_begin = wsl.WSL_CONF_MARKER_BEGIN.format(name="other")
    wsl_other_end = wsl.WSL_CONF_MARKER_END.format(name="other")
    wsl_end = wsl.WSL_CONF_MARKER_END.format(name="hostname")
    malformed = (
        f"{wsl_begin}\n"
        f"{wsl_other_begin}\n"
        "hostname = personal-wsl\n"
        f"{wsl_other_end}\n"
        f"{wsl_end}\n"
    )
    original_read = wsl.read_wsl_conf
    original_write = wsl.write_wsl_conf
    original_run = wsl.run
    writes = []
    try:
        wsl.read_wsl_conf = lambda _instance: malformed
        wsl.write_wsl_conf = lambda _instance, content: writes.append(content)
        wsl.run = lambda *_args, **_kwargs: SimpleNamespace(returncode=0)
        try:
            wsl.configure_wsl_hostname("personal", "personal-wsl")
        except wsl.ManagedMarkerError:
            pass
        else:
            raise AssertionError("WSL nested managed markers were accepted")
    finally:
        wsl.read_wsl_conf = original_read
        wsl.write_wsl_conf = original_write
        wsl.run = original_run
    if writes:
        raise AssertionError("WSL changed a config with nested markers")


def check_wsl_import_ownership_and_uid():
    module = load_module(
        "workspace_personal_wsl_import_safety",
        WORKSPACE_ROOT / "applications" / "wsl" / "install_wsl.py",
    )
    original_run = module.run
    original_local_app_data = os.environ.get("LOCALAPPDATA")
    original_list_distro_info = module.list_wsl_distro_info
    original_resolve_user = module.resolve_effective_wsl_user
    original_resolve_uid = module.resolve_effective_wsl_uid
    original_which = module.shutil.which
    original_ask = module.ask
    try:
        identity_calls = []

        def fake_identity_run(command, **_kwargs):
            identity_calls.append(command)
            if command[-2:] == ["id", "-un"]:
                return SimpleNamespace(returncode=0, stdout=b"renamed-root\n")
            if command[-2:] == ["id", "-u"]:
                return SimpleNamespace(returncode=0, stdout=b"0\n")
            raise AssertionError(f"unexpected identity command: {command}")

        module.run = fake_identity_run
        if module.verify_effective_wsl_user("personal") is not False:
            raise AssertionError("UID-0 account with a non-root name was accepted")

        unregister_calls = []
        module.run = lambda command, **_kwargs: (
            unregister_calls.append(command)
            or SimpleNamespace(returncode=0)
        )
        if module.cleanup_imported_distro("personal", False):
            raise AssertionError("unowned WSL registration was cleaned up")
        if unregister_calls:
            raise AssertionError("unowned WSL registration was unregistered")

        os.environ["LOCALAPPDATA"] = str(SCRATCH_ROOT / "wsl-import-safety")
        export_path = SCRATCH_ROOT / "wsl-import-failed.tar"
        module.list_wsl_distro_info = lambda: [
            {"name": "Ubuntu", "state": "Stopped", "version": 2}
        ]
        module.resolve_effective_wsl_user = lambda _instance: "alice"
        module.resolve_effective_wsl_uid = lambda _instance: 1000
        module.shutil.which = lambda name: "wsl.exe" if name == "wsl" else None
        module.ask = lambda _prompt: "n"
        failed_import_calls = []

        def failed_import_run(command, **_kwargs):
            if command[1] == "--export":
                export_path.write_bytes(b"archive")
                return SimpleNamespace(returncode=0)
            if command[1] == "--import":
                raise subprocess.CalledProcessError(1, command)
            if command[1] == "--unregister":
                failed_import_calls.append(command)
                return SimpleNamespace(returncode=0)
            raise AssertionError(f"unexpected import command: {command}")

        module.run = failed_import_run
        try:
            module.rename_distro("Ubuntu", "personal", export_path)
        except subprocess.CalledProcessError:
            pass
        else:
            raise AssertionError("failed WSL import was reported as success")
        if failed_import_calls:
            raise AssertionError(
                "failed WSL import unregistered a target it did not own"
            )
        if export_path.exists():
            export_path.unlink()
    finally:
        module.run = original_run
        module.list_wsl_distro_info = original_list_distro_info
        module.resolve_effective_wsl_user = original_resolve_user
        module.resolve_effective_wsl_uid = original_resolve_uid
        module.shutil.which = original_which
        module.ask = original_ask
        if original_local_app_data is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = original_local_app_data


def check_wsl_existing_target_expected_user():
    module = load_module(
        "workspace_personal_wsl_expected_target_user",
        WORKSPACE_ROOT / "applications" / "wsl" / "install_wsl.py",
    )
    original_is_windows = module.IS_WINDOWS
    original_which = module.shutil.which
    original_list_distro_info = module.list_wsl_distro_info
    original_resolve_user = module.resolve_effective_wsl_user
    original_resolve_uid = module.resolve_effective_wsl_uid
    original_local_app_data = os.environ.get("LOCALAPPDATA")
    original_argv = sys.argv[:]
    try:
        os.environ["LOCALAPPDATA"] = str(
            SCRATCH_ROOT / "wsl-expected-target-user"
        )
        module.IS_WINDOWS = True
        module.shutil.which = lambda name: "wsl.exe" if name == "wsl" else None
        module.list_wsl_distro_info = lambda: [
            {"name": "Ubuntu", "state": "Stopped", "version": 2},
            {"name": "personal", "state": "Stopped", "version": 2},
        ]
        module.resolve_effective_wsl_user = lambda instance: (
            "alice" if instance.lower() == "ubuntu" else "bob"
        )
        module.resolve_effective_wsl_uid = lambda _instance: 1000
        if module.configure_wsl("Ubuntu", "personal") is not False:
            raise AssertionError(
                "existing target with a wrong source user was accepted"
            )

        expected_user_path = module._expected_wsl_user_path("personal")
        if expected_user_path.read_text(encoding="utf-8").strip() != "alice":
            raise AssertionError(
                "re-derived source user was not persisted for future reruns"
            )
        module.list_wsl_distro_info = lambda: [
            {"name": "personal", "state": "Stopped", "version": 2},
        ]
        sys.argv = [
            str(WORKSPACE_ROOT / "applications" / "wsl" / "install_wsl.py"),
            "--skip-install",
            "--skip-hostname",
            "--skip-working-copy",
            "--skip-yazi",
            "--skip-jetbrains-gateway",
        ]
        if module.main() != 1:
            raise AssertionError(
                "skip-install path did not enforce the persisted source user"
            )
    finally:
        sys.argv = original_argv
        module.IS_WINDOWS = original_is_windows
        module.shutil.which = original_which
        module.list_wsl_distro_info = original_list_distro_info
        module.resolve_effective_wsl_user = original_resolve_user
        module.resolve_effective_wsl_uid = original_resolve_uid
        if original_local_app_data is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = original_local_app_data


def check_shell_extra_skips(powershell):
    scripts = WORKSPACE_ROOT / "scripts"
    caller_terminating_exit = "exit " + "0"
    for name in ("install-ai-alias.ps1", "install-ll-function.ps1",
                 "install-np-alias.ps1", "install-notes-function.ps1"):
        content = (scripts / name).read_text(encoding="utf-8")
        if caller_terminating_exit in content:
            raise AssertionError(f"{name} still contains a caller-terminating exit")

    ll_script = (scripts / "install-ll-function.ps1").read_text(encoding="utf-8")
    legacy_size_helper = "Format-" + "Size"
    if (f"function {legacy_size_helper}" in ll_script or
            f" {legacy_size_helper} " in ll_script):
        raise AssertionError(f"generic {legacy_size_helper} helper remains in the ll setup")
    if "Format-WorkspacePersonalSize" not in ll_script:
        raise AssertionError("workspace-specific ll size helper is missing")

    shell_dir = SCRATCH_ROOT / "shell-extras"
    shell_dir.mkdir()
    shutil.copy2(scripts / "setup-shell-extras.ps1", shell_dir / "setup-shell-extras.ps1")
    shutil.copy2(scripts / "install-ai-alias.ps1", shell_dir / "install-ai-alias.ps1")
    shutil.copy2(scripts / "install-np-alias.ps1", shell_dir / "install-np-alias.ps1")
    (shell_dir / "shell-extras.psd1.ps1").write_text(
        "@(\n"
        "    [pscustomobject]@{ Name = 'ai'; Description = 'check'; "
        "Script = 'install-ai-alias.ps1' }\n"
        "    [pscustomobject]@{ Name = 'np'; Description = 'check'; "
        "Script = 'install-np-alias.ps1' }\n"
        ")\n",
        encoding="utf-8",
    )
    caller = SCRATCH_ROOT / "caller.ps1"
    setup = shell_dir / "setup-shell-extras.ps1"
    caller.write_text(
        f"& '{str(setup).replace(chr(39), chr(39) * 2)}' -Quiet\n"
        "Write-Output 'caller-survived'\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PATH"] = str(Path(powershell).parent)
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(caller)],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0 or "caller-survived" not in result.stdout:
        raise AssertionError(
            "shell-extra skip did not return to its caller:\n"
            f"{result.stdout}\n{result.stderr}"
        )


def main():
    if SCRATCH_ROOT.exists():
        raise RuntimeError(f"focused-check scratch path already exists: {SCRATCH_ROOT}")
    SCRATCH_ROOT.mkdir()
    try:
        powershell = powershell_executable()
        check_shell_wrappers()
        check_profile_helper_behavior(powershell)
        check_ai_shortcut_migrations(powershell)
        check_profile_helper_discovery_failures(powershell)
        check_shell_extra_sibling_conflicts(powershell)
        check_shell_extra_all_profile_conflicts(powershell)
        check_shell_extra_atomic_marker_preflight(powershell)
        check_np_alias_rerun(powershell)
        check_notes_helper(powershell)
        check_wezterm_legacy_config()
        check_wezterm_wsl_output_decoding()
        check_powershell_application_profile_encoding()
        check_application_profile_conflicts()
        check_powershell_malformed_marker()
        check_powershell_global_marker_preflight()
        check_yazi_conflict_forms()
        check_yazi_atomic_marker_preflight()
        check_yazi_environment_broadcast()
        check_yazi_toml_escaping()
        check_line_ending_preservation()
        check_managed_marker_validation()
        check_existing_executable_discovery()
        check_powershell_post_install_discovery()
        check_yazi_profile_discovery()
        check_wezterm_post_install_discovery()
        check_wsl_hostname_merge()
        check_wsl_conf_read_safety()
        check_wsl_name_and_path_safety()
        check_wsl_default_user_handling()
        check_wsl_existing_target_user()
        check_wsl_existing_target_expected_user()
        check_wsl_import_ownership_and_uid()
        check_wsl_cleanup_failure_propagation()
        check_yazi_utf16_profile()
        check_shell_extra_skips(powershell)
    finally:
        def remove_read_only(func, path, _exc_info):
            os.chmod(path, stat.S_IWRITE)
            func(path)

        shutil.rmtree(SCRATCH_ROOT, onerror=remove_read_only)

    print("Focused remediation checks passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Focused remediation check failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
