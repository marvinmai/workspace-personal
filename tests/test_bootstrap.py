"""Unit tests for the bootstrap core. Run: python3 -m unittest discover -s tests"""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import contextlib  # noqa: E402
import importlib.util  # noqa: E402
import io  # noqa: E402

import subprocess  # noqa: E402

from bootstrap import clone, config, link, picker, shell  # noqa: E402
from bootstrap import slice as slice_cmd  # noqa: E402
from bootstrap.__main__ import main as cli_main  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "install_wezterm",
    Path(__file__).resolve().parent.parent / "applications" / "wezterm" / "install_wezterm.py")
wez = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wez)


def _load_installer(app):
    path = Path(__file__).resolve().parent.parent / "applications" / app / f"install_{app}.py"
    spec = importlib.util.spec_from_file_location(f"install_{app}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helix_installer = _load_installer("helix")
brew_common = sys.modules["brew_common"]

REPOS = [{"name": n} for n in ("alpha-api", "beta-api", "alpha-ui", "gamma")]


class IsolatedHome(unittest.TestCase):
    """Run every test with HOME/APPDATA/XDG_CONFIG_HOME pointing at a temp dir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "APPDATA", "XDG_CONFIG_HOME")}
        os.environ.update(HOME=str(self.home), USERPROFILE=str(self.home),
                          APPDATA=str(self.home / "AppData"), XDG_CONFIG_HOME=str(self.home / ".config"))

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()


class PickerFilter(unittest.TestCase):
    def names(self, text):
        return [r["name"] for r in picker.filter_repos(REPOS, text)]

    def test_empty_returns_all(self):
        self.assertEqual(self.names(""), [r["name"] for r in REPOS])

    def test_comma_is_and(self):
        self.assertEqual(self.names("alpha,api"), ["alpha-api"])

    def test_prefix_matches_first_case_insensitive(self):
        self.assertEqual(self.names("API"), ["alpha-api", "beta-api"])
        self.assertEqual(self.names("a"), ["alpha-api", "alpha-ui", "beta-api", "gamma"])


class Config(IsolatedHome):
    def test_defaults_do_not_contain_employer_or_user(self):
        cfg = config.default_config()
        self.assertEqual(Path(cfg["ai_personal_dir"]).name, "ai-personal")
        self.assertEqual(Path(cfg["notes_dir"]).name, "ai-projects")

    def test_file_overrides_defaults_and_roundtrips(self):
        config.save_config({"notes_dir": "/x/notes"})
        cfg = config.load_config()
        self.assertEqual(cfg["notes_dir"], "/x/notes")
        self.assertIn("repos_dir", cfg)

    def test_ensure_config_creates_file_once(self):
        self.assertFalse(config.config_path().exists())
        config.ensure_config()
        self.assertTrue(config.config_path().is_file())

    def test_bom_tolerated(self):
        p = config.config_path()
        p.parent.mkdir(parents=True)
        p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"notes_dir": "/b"}).encode())
        self.assertEqual(config.load_config()["notes_dir"], "/b")


class Clone(unittest.TestCase):
    def test_parse_source(self):
        self.assertEqual(clone.parse_source("github:me"), {"type": "github", "owner": "me"})
        with self.assertRaises(SystemExit):
            clone.parse_source("gitlab:me")
        with self.assertRaises(SystemExit):
            clone.parse_source("github:")


class ShellBlock(unittest.TestCase):
    def test_replace_is_idempotent_and_preserves_user_text(self):
        block = shell.posix_block()
        once = shell.replace_block("export A=1\n", block)
        twice = shell.replace_block(once, block)
        self.assertEqual(once, twice)
        self.assertTrue(once.startswith("export A=1\n"))
        self.assertEqual(once.count(shell.BEGIN), 1)

    def test_replace_updates_existing_block_in_place(self):
        old = shell.BEGIN + "\nold\n" + shell.END + "\n# after\n"
        new = shell.replace_block("# before\n" + old, shell.posix_block())
        self.assertIn("# before", new)
        self.assertIn("# after", new)
        self.assertNotIn("\nold\n", new)

    def test_posix_block_defines_short_bootstrap_commands(self):
        block = shell.posix_block()
        for command in ("link", "doctor", "setup", "help", "config"):
            self.assertIn(f'ws-{command}() {{ _aiw {command} "$@"; }}', block)
        self.assertNotIn("ai-link", block)
        self.assertNotRegex(block, r"(?m)^config\(\)")

    def test_help_lists_every_shell_command(self):
        functions = re.findall(r"^([\w-]+)\(\)", shell.posix_block(), re.MULTILINE)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli_main(["help"])
        self.assertEqual(code, 0)
        for name in functions:
            if name != "_aiw":
                self.assertRegex(out.getvalue(), rf"(?m)^  {re.escape(name)}\b")

    def test_malformed_block_raises(self):
        with self.assertRaises(ValueError):
            shell.replace_block(shell.BEGIN + "\nno end", shell.posix_block())


class Link(IsolatedHome):
    def make_ai_repo(self):
        ai = self.home / "ai-personal"
        (ai / "rules").mkdir(parents=True)
        (ai / "rules" / "safety.md").write_text("# Safety\n", encoding="utf-8")
        (ai / "settings").mkdir()
        (ai / "settings" / "permissions.fragment.json").write_text(
            json.dumps({"permissions": {"ask": ["Bash(git push:*)"]}}), encoding="utf-8")
        return ai

    def test_merge_permissions_union_and_idempotent(self):
        settings = {"permissions": {"ask": ["X"]}, "theme": "auto"}
        frag = {"permissions": {"ask": ["Y", "X"]}}
        self.assertTrue(link.merge_permissions(settings, frag))
        self.assertEqual(settings["permissions"]["ask"], ["X", "Y"])
        self.assertFalse(link.merge_permissions(settings, frag))
        self.assertEqual(settings["theme"], "auto")

    def test_rules_deployed_then_unchanged(self):
        ai = self.make_ai_repo()
        self.assertIn("deployed", link.link_rules(ai, False)[0])
        self.assertIn("up to date", link.link_rules(ai, False)[0])
        deployed = (self.home / ".claude" / "rules" / "safety.md").read_text(encoding="utf-8")
        self.assertTrue(deployed.startswith(link.RULE_MARK))

    def test_unmanaged_rule_never_overwritten(self):
        ai = self.make_ai_repo()
        target = self.home / ".claude" / "rules" / "safety.md"
        target.parent.mkdir(parents=True)
        target.write_text("mine", encoding="utf-8")
        self.assertIn("untouched", link.link_rules(ai, False)[0])
        self.assertEqual(target.read_text(encoding="utf-8"), "mine")

    def test_settings_merge_keeps_backup(self):
        ai = self.make_ai_repo()
        sp = self.home / ".claude" / "settings.json"
        sp.parent.mkdir(parents=True)
        sp.write_text(json.dumps({"theme": "auto"}), encoding="utf-8")
        self.assertIn("merged", link.link_settings(ai, False))
        merged = json.loads(sp.read_text(encoding="utf-8"))
        self.assertEqual(merged["theme"], "auto")
        self.assertEqual(merged["permissions"]["ask"], ["Bash(git push:*)"])
        self.assertEqual(len(list(sp.parent.glob("settings.json.bak-*"))), 1)
        self.assertIn("up to date", link.link_settings(ai, False))


class WeztermInstaller(IsolatedHome):
    def setUp(self):
        super().setUp()
        self._win = wez.IS_WINDOWS
        os.environ.pop("WEZTERM_CONFIG_FILE", None)
        os.environ.pop("XDG_CONFIG_HOME", None)
        wez.IS_WINDOWS = False

    def tearDown(self):
        wez.IS_WINDOWS = self._win
        super().tearDown()

    def test_default_path_is_xdg_location_when_nothing_exists(self):
        self.assertEqual(wez.wezterm_config_path(),
                         self.home / ".config" / "wezterm" / "wezterm.lua")

    def test_existing_legacy_file_is_reused(self):
        (self.home / ".wezterm.lua").write_text("x", encoding="utf-8")
        self.assertEqual(wez.wezterm_config_path(), self.home / ".wezterm.lua")

    def test_xdg_file_wins_over_legacy(self):
        xdg = self.home / ".config" / "wezterm" / "wezterm.lua"
        xdg.parent.mkdir(parents=True)
        xdg.write_text("x", encoding="utf-8")
        (self.home / ".wezterm.lua").write_text("y", encoding="utf-8")
        self.assertEqual(wez.wezterm_config_path(), xdg)

    def test_windows_keeps_legacy_path(self):
        wez.IS_WINDOWS = True
        self.assertEqual(wez.wezterm_config_path(), self.home / ".wezterm.lua")

    def test_approaches_per_platform(self):
        self.assertEqual(wez.supported_shell_approaches(), ("login", "pwsh"))
        wez.IS_WINDOWS = True
        self.assertEqual(wez.supported_shell_approaches(), ("wsl", "pwsh", "cmd"))

    def test_login_approach_sets_no_default_prog(self):
        lines = wez.shell_approach_lines("login", "x")
        self.assertFalse(any("default_prog" in l.split("--")[0] for l in lines))
        wez.IS_WINDOWS = True
        with self.assertRaises(ValueError):
            wez.shell_approach_lines("login", "x")

    def test_base_then_login_is_idempotent_and_keeps_user_content(self):
        cfg = self.home / "wezterm.lua"
        cfg.write_text("local wezterm = require 'wezterm'\nlocal config = wezterm.config_builder()\n"
                       "config.tab_max_width = 40\nreturn config\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(2):
                wez.upsert_marked_block(cfg, "base", wez.base_lines())
                wez.upsert_marked_block(cfg, "shell-approach", wez.shell_approach_lines("login", "x"),
                                        conflict_pattern=wez.CONFLICT_PATTERN)
        text = cfg.read_text(encoding="utf-8")
        self.assertEqual(text.count(">>> wezterm-setup: base >>>"), 1)
        self.assertEqual(text.count(">>> wezterm-setup: shell-approach >>>"), 1)
        self.assertIn("config.tab_max_width = 40", text)
        self.assertIn('config.color_scheme = "Catppuccin Mocha"', text)
        self.assertTrue(text.rstrip().endswith("return config"))

    def test_base_file_has_no_shell_choice_and_no_employer(self):
        text = wez.BASE_FILE.read_text(encoding="utf-8")
        self.assertNotIn("default_prog", text)
        self.assertNotIn("default_domain", text)


class Slice(unittest.TestCase):
    """`slice` runs the current repo's scripts/slice.* with the interpreter its extension names."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name).resolve() / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / "sub").mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)

    def tearDown(self):
        self._tmp.cleanup()

    def launcher(self, name, text=""):
        path = self.repo / "scripts" / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_command_per_extension(self):
        cases = {
            "slice.mjs": "node", "slice.js": "node", "slice.cjs": "node",
            "slice.py": sys.executable, "slice.sh": "bash",
        }
        for name, interpreter in cases.items():
            with self.subTest(name=name):
                path = self.repo / "scripts" / name
                self.assertEqual(slice_cmd.command(path, ["7"]), [interpreter, str(path), "7"])

    def test_powershell_launcher_runs_as_a_file(self):
        path = self.repo / "scripts" / "slice.ps1"
        cmd = slice_cmd.command(path, ["7"])
        self.assertIn(Path(cmd[0]).stem.lower(), ("pwsh", "powershell"))
        self.assertEqual(cmd[-3:], ["-File", str(path), "7"])

    def test_finds_the_launcher_from_a_subfolder(self):
        path = self.launcher("slice.mjs")
        self.assertEqual(slice_cmd.find_launcher(self.repo / "sub"), path)

    def test_unknown_extensions_are_ignored(self):
        self.launcher("slice.md")
        path = self.launcher("slice.py")
        self.assertEqual(slice_cmd.find_launcher(self.repo), path)

    def test_no_launcher_is_an_error(self):
        with self.assertRaisesRegex(slice_cmd.SliceError, "no scripts/slice"):
            slice_cmd.find_launcher(self.repo)

    def test_two_launchers_are_an_error(self):
        self.launcher("slice.mjs")
        self.launcher("slice.py")
        with self.assertRaisesRegex(slice_cmd.SliceError, "slice.mjs.*slice.py"):
            slice_cmd.find_launcher(self.repo)

    def test_outside_a_git_repo_is_an_error(self):
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        with self.assertRaisesRegex(slice_cmd.SliceError, "not in a git repository"):
            slice_cmd.find_launcher(outside)

    def test_cli_forwards_arguments_cwd_and_exit_code(self):
        out = Path(self._tmp.name) / "out.json"
        self.launcher("slice.py", "import json, os, sys\n"
                      f"open({str(out)!r}, 'w').write(json.dumps([sys.argv[1:], os.getcwd()]))\n"
                      "sys.exit(3)\n")
        cwd = os.getcwd()
        try:
            os.chdir(self.repo / "sub")
            code = cli_main(["slice", "--pick", "7"])
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")),
                         [["--pick", "7"], str(self.repo / "sub")])

    def test_cli_reports_errors_without_a_traceback(self):
        cwd = os.getcwd()
        err = io.StringIO()
        try:
            os.chdir(self.repo)
            with contextlib.redirect_stderr(err):
                code = cli_main(["slice"])
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 1)
        self.assertIn("no scripts/slice", err.getvalue())

    def test_posix_block_defines_slice(self):
        self.assertIn('slice() { _aiw slice "$@"; }', shell.posix_block())


class LinkWezterm(IsolatedHome):
    def test_command_uses_configured_shell(self):
        orig_which, orig_platform = link.shutil.which, link.sys.platform
        try:
            link.shutil.which = lambda name: "/usr/bin/wezterm" if name == "wezterm" else None
            link.sys.platform = "linux"
            cmd = link.wezterm_command({"wezterm_shell": "pwsh"})
            self.assertEqual(cmd[-2:], ["--shell-approach", "pwsh"])
            self.assertEqual(link.wezterm_command({})[-1], "login")
            link.shutil.which = lambda name: None
            self.assertIsNone(link.wezterm_command({}))
            link.shutil.which = lambda name: "/x"
            link.sys.platform = "win32"
            self.assertIsNone(link.wezterm_command({}))
        finally:
            link.shutil.which, link.sys.platform = orig_which, orig_platform


class Brew(unittest.TestCase):
    def test_shellenv_line_uses_the_brew_path(self):
        self.assertEqual(brew_common.shellenv_line("/opt/brew/bin/brew"),
                         'eval "$(/opt/brew/bin/brew shellenv)"')

    def test_shellenv_is_appended_once(self):
        line = 'eval "$(/b/brew shellenv)"'
        text = brew_common.add_shellenv("alias ll='ls -l'\n", line)
        self.assertEqual(text, f"alias ll='ls -l'\n\n{line}\n")
        self.assertIsNone(brew_common.add_shellenv(text, line))

    def test_existing_shellenv_in_any_form_is_kept(self):
        rc = 'eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv bash)"\n'
        self.assertIsNone(brew_common.add_shellenv(rc, 'eval "$(/x/brew shellenv)"'))

    def test_commented_shellenv_does_not_count(self):
        rc = '# eval "$(/b/brew shellenv)"\n'
        self.assertIsNotNone(brew_common.add_shellenv(rc, 'eval "$(/b/brew shellenv)"'))

    def test_missing_keeps_the_wanted_order(self):
        self.assertEqual(brew_common.missing({"b", "x"}, ["a", "b", "c"]), ["a", "c"])


class HelixInstaller(unittest.TestCase):
    def test_installs_helix_and_a_server_per_language(self):
        formulae = helix_installer.formulae()
        self.assertEqual(formulae[0], "helix")
        for server in ("typescript-language-server", "ty", "ruff", "jdtls",
                       "bash-language-server", "marksman", "yaml-language-server",
                       "vscode-langservers-extracted"):
            self.assertIn(server, formulae)
        self.assertEqual(len(formulae), len(set(formulae)))


class SetupInstallers(unittest.TestCase):
    def test_linux_runs_yazi_and_helix(self):
        from bootstrap import setup
        orig = setup.IS_WINDOWS
        try:
            setup.IS_WINDOWS = False
            names = [Path(p).name for p in setup._installers()]
        finally:
            setup.IS_WINDOWS = orig
        self.assertEqual(names, ["install_yazi.py", "install_helix.py"])


if __name__ == "__main__":
    unittest.main()
