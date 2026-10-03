"""CLI: python3 -m bootstrap <setup|clone|link|doctor|config|slice|help> (Windows: py -3 -m bootstrap ...)."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__, config


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ai-workspace", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("setup", help="config, clone, link and optional application installers")
    s.add_argument("--skip-clone", action="store_true")
    s.add_argument("--skip-link", action="store_true")
    s.add_argument("--install-applications", action="store_true")
    s.add_argument("--non-interactive", action="store_true")

    c = sub.add_parser("clone", help="pick and clone repositories from the configured sources")
    c.add_argument("--repos-dir", help="override repos_dir from the config")
    c.add_argument("--source", action="append", help="github:<owner>; repeatable; overrides config")
    c.add_argument("--select", help="comma-separated repo names (non-interactive)")
    c.add_argument("--all", action="store_true", help="clone every listed repo (non-interactive)")

    l = sub.add_parser("link", help="install plugin, rules, settings fragment and shell helpers")
    l.add_argument("--dry-run", action="store_true")

    d = sub.add_parser("doctor", help="report what is installed and what drifted")
    d.add_argument("--json", action="store_true")

    sl = sub.add_parser("slice", help="run the current repo's scripts/slice.<ext>, passing the arguments on")
    sl.add_argument("args", nargs=argparse.REMAINDER)

    sub.add_parser("help", help="overview of the workspace shell commands")

    g = sub.add_parser("config", help="show or read the local config")
    g.add_argument("action", choices=["show", "path", "get", "init"])
    g.add_argument("key", nargs="?")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # argparse would claim the launcher's own options (`slice --pick`), so pass them on untouched.
    if argv[:1] == ["slice"]:
        from . import slice as slice_cmd
        return slice_cmd.run(argv[1:])
    args = build_parser().parse_args(argv)

    if args.command == "config":
        if args.action == "path":
            print(config.config_path())
        elif args.action == "init":
            cfg = config.ensure_config()
            print(f"Config at {config.config_path()}")
            print(json.dumps(cfg, indent=2))
        elif args.action == "show":
            print(json.dumps(config.load_config(), indent=2))
        else:
            if not args.key:
                print("config get needs a key", file=sys.stderr)
                return 2
            try:
                print(config.get_value(config.load_config(), args.key))
            except KeyError:
                print(f"Unknown key: {args.key}", file=sys.stderr)
                return 1
        return 0

    if args.command == "help":
        from . import shell
        print(shell.help_text())
        return 0
    if args.command == "clone":
        from . import clone
        return clone.run(args)
    if args.command == "link":
        from . import link
        return link.run(dry_run=args.dry_run)
    if args.command == "doctor":
        from . import doctor
        return doctor.run(as_json=args.json)
    if args.command == "setup":
        from . import setup as setup_cmd
        return setup_cmd.run(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
