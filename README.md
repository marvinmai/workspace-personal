# Personal workspace

Cross-platform (Linux, Windows, WSL2) bootstrap for my personal development
setup: a stdlib-only Python core, optional application installers and shell
helpers. Nothing employer- or machine-specific is committed; per-machine values
live in a local config file.

## Boundaries

- `ai-personal` owns Claude Code skills, rules and hooks (a plugin plus
  marketplace). This repository only wires it into the machine (`link`).
- `ai-projects` is the private notes vault. This repository only knows its path.
- No credentials, tokens, repository inventory or mutable runtime state are
  tracked. Git authentication stays with the local Git installation.
- Employer-specific tooling does not belong here. See the `porting-environments`
  skill in `ai-personal` and `docs/PORTING.md`.

## Quick start

```sh
# Linux / WSL / macOS                    # Windows (cmd or PowerShell)
bash setup.sh                            .\setup.bat
bash clone.sh                            .\clone.bat
```

Both wrappers run `python3 -m bootstrap <command>` (Windows: `py -3 -m ...`);
no PowerShell is needed on Linux. Commands:

| Command | What it does |
| --- | --- |
| `setup` | create config, optionally clone repos, `link`, optional application installers |
| `clone` | pick repositories from the configured sources (`--select a,b`, `--all`, `--source github:<owner>`) |
| `link` | install the `personal` plugin from `ai-personal`, deploy rules to `~/.claude/rules`, merge the permissions fragment, install shell helpers (`--dry-run` to preview) |
| `doctor` | report tools, config, plugin/rules/settings state and drift: CRLF, missing exec bits, denylist hits |
| `config show|path|get <key>|init` | inspect the local config |

Shell helpers: `ai-personal` and `notes` change into the configured folders,
`ai` runs Claude Code, `clone` starts the picker, `ws-link`, `ws-doctor`,
`ws-setup` and `ws-config` run those commands from any directory, and `ws-help`
lists them all. On Linux they are a managed
block in `~/.bashrc`/`~/.zshrc`; on Windows the `scripts\install-*.ps1`
installers write the PowerShell profile.

## Local config

`~/.config/ai-workspace/config.json` (Windows: `%APPDATA%\ai-workspace\config.json`),
created with defaults on first run; never committed:

```json
{
  "repos_dir": "/home/me/dev",
  "ai_personal_dir": "/home/me/dev/ai-personal",
  "notes_dir": "/home/me/dev/ai-projects",
  "sources": [{"type": "github", "owner": "<user-or-org>"}]
}
```

Defaults: `repos_dir` is the parent of this checkout; `sources` starts with the
owner of this repo's `origin`. A new employer or org is one more entry in
`sources`; remove it when you leave. `gh` is optional: with it (authenticated)
private repos are listed, without it only public ones.

## Layout

```text
workspace-personal/
|-- bootstrap/              Python core: config, clone, picker, link, shell, doctor, setup
|-- tests/                  unittest suite for the core
|-- setup.sh / setup.bat    launchers
|-- clone.sh / clone.bat
|-- applications/           powershell, wezterm, wsl, yazi installers (Python)
|-- scripts/                PowerShell profile helpers (Windows), validate.ps1, check-remediations.py
|-- docs/PORTING.md         how to move between OSes, machines and employers
`-- .denylist.example       copy to .denylist (untracked) to block leaks at commit time
```

## Prerequisites

- Python 3.9+ and Git everywhere. Optional: `gh`, `claude`, Node.
- Windows additionally: WSL2 for the WSL installer, PowerShell 7 for the profile
  helpers (`winget install Microsoft.PowerShell`), `winget` is used when present.
  If Python is missing, `setup.bat` prints the `winget install Python.Python.3.12` command.

## Windows and WSL2 usage

Typical order on a fresh Windows machine:

1. Install Git and Python, then run `.\setup.bat`.
2. Set up WSL2 from an elevated-capable terminal when needed:

   ```powershell
   py -3 applications\wsl\install_wsl.py
   ```

   Complete the Linux first-launch username/password flow when prompted, then
   re-run the installer.
3. Configure WezTerm and PowerShell after the WSL instance name is settled:

   ```powershell
   py -3 applications\powershell\install_powershell.py
   py -3 applications\wezterm\install_wezterm.py --shell-approach wsl
   ```

4. Run the Yazi installer on the host or inside WSL as appropriate:

   ```powershell
   py -3 applications\yazi\install_yazi.py --target-path C:\path\to\workspace-personal
   ```

The WSL installer itself is Windows-only. Run it from a Windows terminal; the
other Python installers retain their cross-platform behavior where practical.
It checks the registered distro versions, converts an existing target from
WSL1 to WSL2, verifies that the effective default user is non-root before
setup continues, and imports renamed distros explicitly as WSL2. Failed
partial imports are repaired or rejected with recovery guidance instead of
being accepted on a later run. `--skip-install` still enforces the persisted
expected Linux user for an existing target; if the source distro is still
present and the state is missing, the user is revalidated and persisted before
the target is accepted.

Application installers have their own prompts, so `setup --non-interactive
--install-applications` is rejected before anything runs. Use
`setup --install-applications` for the interactive path, or `--skip-clone` and
`--skip-link` to narrow the setup. Run an individual Python installer
separately with its `--help` options when you want to answer its prompts.

## Configuration overrides

Application-specific overrides include:

- PowerShell: `--start-dir` or `WORKSPACE_PERSONAL_START_DIR`
- WezTerm: `--config-file`, `WEZTERM_CONFIG_FILE`,
  `--wsl-instance-name`, or `WORKSPACE_PERSONAL_WSL_INSTANCE`
- WSL2: `--windows-source-dir`, `--wsl-working-target`
  (`--wsl-dev-target` remains an alias),
  `--wsl-yazi-target`, `--yazi-installer`, and `--export-tar`
- Yazi: `--target-path`, `--config-dir`,
  `WORKSPACE_PERSONAL_YAZI_TARGET`, and `YAZI_CONFIG_HOME`

Run any Python installer with `--help` for its complete option list.
On Linux and macOS, the WezTerm installer accepts only the `pwsh` shell
approach; `wsl` and `cmd` are Windows-only.

## Idempotency and conflict safety

Installers update only their own marker blocks. Before writing, profile and
configuration targets plus conflict-scan paths are resolved, read, and checked
for balanced, unique, non-nested, well-ordered managed markers; malformed
markers fail without changing any profile. They preserve user content outside
those blocks, detect conflicting hand-written commands or settings, and leave
those conflicts untouched with a message. Re-running is therefore safe: managed
blocks are updated in place (including a changed Notepad++ path). PowerShell conflict scans query all four `$PROFILE` properties
from every installed interpreter (plus known host-specific profile paths), while
writes remain limited to each interpreter's `CurrentUserAllHosts` target.
Already-discoverable WezTerm/Yazi executables allow configuration to continue
without a package manager, and the PowerShell profile writers preserve detected
UTF-8, UTF-16LE/BE, and UTF-32LE/BE profile encoding and BOM state.

If an installer attempts a required package or setup operation and it fails, it
returns a nonzero exit code so the setup wrapper can stop. Explicitly declining
an optional prompt remains a successful skip. The WezTerm installer refuses
legacy or otherwise unrecognized configs rather than risking an invalid managed
block. Existing configs are only modified when they use the standard
`local config = wezterm.config_builder()` / `return config` shape; one terminal
semicolon after the return is also accepted. Dynamic WSL names are escaped as
Lua string literals before they are written.

The installers can edit user-owned PowerShell profiles, `.wezterm.lua`,
Yazi configuration, WSL `/etc/wsl.conf`, and selected Windows user environment
variables. On Windows, environment updates notify long-lived processes with
`WM_SETTINGCHANGE` and update the installer's current process as well. Review
prompts and overrides before accepting changes.

## Lightweight validation

Unit tests for the bootstrap package need only Python:

```sh
python3 -m unittest discover -s tests
```

Windows-specific validation additionally needs PowerShell 7:

```powershell
pwsh -NoProfile -File scripts\validate.ps1
```

It compiles the Python installers, runs each installer with `--help`, and
parses the PowerShell scripts without installing packages or changing user
configuration.
The focused remediation checks are separate and dependency-free:

```powershell
python scripts\check-remediations.py
```

They exercise LF-only Bash wrappers and syntax, exact profile encoding
preservation, atomic malformed-profile-marker refusal, rerunnable Notepad++
updates, safe and commented/parenthesized WezTerm config shapes, WezTerm/Yazi
line-ending preservation, TOML escaping, post-install executable discovery,
Yazi command-conflict forms and mocked Windows environment broadcasts, WSL
hostname/default-user handling, existing-target user validation, and WSL
cleanup failure handling.
They use only isolated scratch data beneath this workspace and remove it when
finished.
