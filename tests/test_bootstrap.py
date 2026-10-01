"""Unit tests for the bootstrap core. Run: python3 -m unittest discover -s tests"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bootstrap import clone, config, link, picker, shell  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
