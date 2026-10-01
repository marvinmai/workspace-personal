# Porting guide: new OS, new machine, new employer

The procedure lives as a skill in `ai-personal` (`skills/porting-environments`,
with `references/checklist.md` and `references/os-matrix.md`). Ask Claude to
"use the porting-environments skill" when you start. This file is the short
human version and the record of what the first port (Windows work PC to Linux
private PC, October 2026) taught.

## The layers

| Layer | Where | Rule |
| --- | --- | --- |
| Generic core | `ai-personal` | no employer, usernames, absolute paths, tokens |
| OS adapter | Python in `ai-personal/bin`, `workspace-personal/bootstrap` | one script that detects the OS, no `windows`/`linux` file pairs |
| Per-machine config | `~/.config/ai-workspace/config.json` (Windows: `%APPDATA%`) | never committed |
| Private knowledge | `ai-projects` | no third-party clones, no other people's confidential data |

## Moving to a new machine or OS

1. Install Git and Python (Windows: `winget install Python.Python.3.12 Git.Git`).
2. Clone `workspace-personal` and run `setup.sh` / `setup.bat`.
3. `python3 -m bootstrap doctor` (Windows: `py -3 -m bootstrap doctor`) until nothing is red.
4. Windows only: `winget install Microsoft.PowerShell`, then the PowerShell profile
   helpers install through `link`. Check that hooks work: the plugin's hooks call
   `python3`; if that name is missing on Windows, switch the commands in
   `ai-personal/hooks/hooks.json` to `py -3`.

## Joining a new employer

- Add the employer's GitHub org to `sources` in the config (clone picker).
- Keep anything employer-specific (skills, MCP servers, terminology) in a
  separate private repo/plugin, never in these repos.
- Put the employer's names into `.denylist` of each repo (copy `.denylist.example`)
  so a commit that leaks them is blocked.

## Leaving an employer

Uninstall the overlay plugin and its MCP servers, revoke tokens, grep all repos
and their history for the employer's names and your work email
(`git log --all --format='%ae %ce' | sort -u`), move cloud accounts to a
personal email, and delete the work-data backup.

## Lessons from the first port

- A bootstrap that needs `pwsh` + `gh` fails on a fresh Linux box; use Python.
- A hardcoded GitHub org in the clone script was the lock-in: orgs belong in config.
- Copying the repos wired nothing into `~/.claude`; `link` and `doctor` make that visible.
- Plugin installs are cached copies; develop with `claude --plugin-dir`.
- Employer identities hide in READMEs, skill text and git author emails; grep history too.
- Keep third-party clones out of notes repos; validate folder dates.
- Real Windows verification is only possible on Windows: CI covers unit tests, not the profile helpers.
