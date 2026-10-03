# Usage: what is set up and how to use it

Three repos in `~/dev`, one config file, a few shell commands. Windows differences are marked.

| Repo | Role |
| --- | --- |
| `ai-personal` | Claude Code plugin `personal` (skills, hooks) plus the always-on rules |
| `workspace-personal` | Python bootstrap that wires everything into the machine |
| `ai-projects` | Private notes vault (Obsidian + git) |

Third-party study clones live outside any repo in `~/dev/ref/`.

## Shell commands

Available in any new shell (block in `~/.bashrc`; on Windows the PowerShell profile):

| Command | Does |
| --- | --- |
| `ai-personal` | `cd` into the ai-personal repo (`ai_personal_dir` in the config) |
| `notes` | `cd` into the notes vault (`notes_dir` in the config) |
| `ai [args]` | Runs `claude [args]` |
| `clone [args]` | Starts the repo picker (see below) |
| `slice [args]` | Runs the current repo's own launcher `scripts/slice.<ext>` with the arguments; the extension picks the interpreter (`mjs`/`js`/`cjs`: node, `py`: Python, `sh`: bash, `ps1`: PowerShell). What a slice is stays the repo's business |
| `ws-link`, `ws-doctor`, `ws-setup`, `ws-config` | Run the bootstrap command of the same name from any directory, with the arguments (`ws-link --dry-run`, `ws-config get notes_dir`) |

## The bootstrap CLI

From any directory, `ws-link`, `ws-doctor`, `ws-setup` and `ws-config` run the commands below
(installed by `link`, so the very first run uses the long form). The long form runs from
`~/dev/workspace-personal`. Linux: `python3 -m bootstrap <command>` or `bash setup.sh` / `bash clone.sh`.
Windows: `py -3 -m bootstrap <command>` or `.\setup.bat` / `.\clone.bat`.

### `doctor`: health check (read-only)
```
python3 -m bootstrap doctor          # add --json for machine-readable output
```
Prints `OK`/`WARN`/`FAIL` per check: tools (git, claude, gh, node, pwsh), config and its folders,
plugin installed, rules deployed, settings merged, shell helpers, and per repo: CRLF in scripts,
missing exec bits, denylist hits. Exit code 1 only on `FAIL`. Run it after any change.

### `link`: wire ai-personal into this machine (idempotent)
```
python3 -m bootstrap link --dry-run   # preview
python3 -m bootstrap link
```
Registers the `ai-personal` marketplace and installs/updates `personal@ai-personal`, copies `rules/*.md`
to `~/.claude/rules/`, merges the permissions fragment into `~/.claude/settings.json` (a timestamped
backup is kept), installs the shell helpers, and (Linux/macOS, only if WezTerm is installed) deploys the
WezTerm config. Existing unmanaged rule files are never overwritten.

### `clone`: pick and clone repositories
```
python3 -m bootstrap clone                          # interactive picker
python3 -m bootstrap clone --select repo-a,repo-b   # non-interactive
python3 -m bootstrap clone --all                    # everything not yet cloned
python3 -m bootstrap clone --source github:<owner>  # override the configured sources
python3 -m bootstrap clone --repos-dir ~/other      # override the target folder
```
Picker keys: type to filter (comma = AND), `Tab` switches to list mode, arrows move, `Space` toggles,
`a` all, `n` none, `Enter` confirms, `Esc` quits. `[E]` marks repos already present. With `gh`
installed and logged in, private repos are listed; without it, only public ones.

### `setup`: first-time run
```
python3 -m bootstrap setup [--skip-clone] [--skip-link] [--install-applications] [--non-interactive]
```
Creates the config, optionally runs the picker, runs `link`, and optionally the application installers
(`applications/*`; on Linux only Yazi, on Windows PowerShell/WezTerm/WSL/Yazi). `--non-interactive` with
`--install-applications` is rejected because those installers prompt.

### `config`: inspect the local config
```
python3 -m bootstrap config show | path | get <key> | init
```

## The config file

`~/.config/ai-workspace/config.json` (Windows: `%APPDATA%\ai-workspace\config.json`). Created with defaults
on first run, never committed. Edit it by hand:

```json
{
  "repos_dir": "/home/me/dev",
  "ai_personal_dir": "/home/me/dev/ai-personal",
  "notes_dir": "/home/me/dev/ai-projects",
  "sources": [{ "type": "github", "owner": "marvinmai" }],
  "wezterm_shell": "login"
}
```
New employer or org = add one entry to `sources`; remove it when you leave. Changes take effect
immediately, no re-link needed.

## WezTerm

The terminal's look and behavior live in the repo: `applications/wezterm/wezterm.base.lua` (theme, font,
opacity, tab bar, split keys `Ctrl+Shift+D` / `Ctrl+Shift+E`). `link` (or
`python3 applications/wezterm/install_wezterm.py --shell-approach login`) writes it into the WezTerm config
as a managed `base` block, plus a `shell-approach` block. Anything you add by hand outside those blocks is kept.

- Config file: Linux/macOS `~/.config/wezterm/wezterm.lua` (an existing `~/.wezterm.lua` is reused), Windows `~/.wezterm.lua`.
- Shell on Linux/macOS: `"wezterm_shell": "login"` (default) uses your `$SHELL`, so bash; `"pwsh"` forces PowerShell 7.
  Change it in the config, then run `link` again and restart WezTerm.
- To change the look: edit `wezterm.base.lua` and run `link`. A backup of your pre-framework config is in `~/.config/wezterm/`.
- Windows: run `py -3 applications\wezterm\install_wezterm.py` yourself; the shell (WSL/pwsh/cmd) is an interactive choice there.

## In Claude Code

Skills (invoke as `/personal:<name>`, Claude also picks them by description): `committing-work`,
`config-locations`, `model-cost-optimization`, `obsidian-vault`, `porting-environments`,
`quality-review-loop`, `systematic-debugging`, `test-driven-development`,
`verification-before-completion`, `working-in-worktrees`.

Always-on rules (`~/.claude/rules/`): `safety.md` (propose commits and ask first, never push unasked,
failing test first, sentence-case commit messages, ask for a ticket before a branch) and
`response-style.md`.

Hooks (active automatically): a sound on notifications and when a turn finishes (`CLAUDE_NOTIFY_SILENT=1`
mutes it), and a guard that forces a permission prompt for every `git push`, including `git -C path push`.

## Changing ai-personal

- Live edit while developing: `claude --plugin-dir ~/dev/ai-personal`.
- The installed plugin is a cached copy. After editing, run `ws-link` (or
  `claude plugin update personal@ai-personal`), then `/reload-plugins` in an open session.
- Rule changes: run `link` to redeploy to `~/.claude/rules/`.
- Never put `.mcp.json` or `settings.json` in the repo root; they would become live plugin components.

## Keeping employer data out

Each repo has a local, untracked `.denylist` (copy `.denylist.example`): one regex per line of names
that must never be committed. The pre-commit hook blocks matching commits.
Enable it in a new clone: `scripts/install-hooks.sh` (ai-personal) or `git config core.hooksPath .githooks`
(ai-projects). In ai-projects the check covers `knowledge/` only; the career portfolio is exempt on purpose.

## Tests

```
cd ~/dev/workspace-personal && python3 -m unittest discover -s tests
cd ~/dev/ai-personal        && python3 -m unittest discover -s tests
claude plugin validate ~/dev/ai-personal
```
With `pwsh` installed: `pwsh -NoProfile -File scripts/validate.ps1` and
`python3 scripts/check-remediations.py` (PowerShell profile helper tests).

## New machine, OS or employer

Ask Claude to use the `porting-environments` skill, or read `docs/PORTING.md`. Short version:
install Git and Python, clone `workspace-personal`, run `setup`, then `doctor` until nothing is red.
