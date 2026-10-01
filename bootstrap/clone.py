"""List repositories from the configured sources and clone a selection.

Sources come from the local config (never from code), e.g.
  {"type": "github", "owner": "<user-or-org>"}
Listing uses `gh` when it is installed and authenticated (so private repos show
up); otherwise the public GitHub REST API via urllib (GITHUB_TOKEN honoured).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from . import config, picker

IS_WINDOWS = sys.platform.startswith("win")


def parse_source(text: str) -> dict:
    kind, _, owner = text.partition(":")
    if kind != "github" or not owner:
        raise SystemExit(f"Unsupported source '{text}'. Use github:<owner>.")
    return {"type": "github", "owner": owner}


def _gh_ready() -> bool:
    if not shutil.which("gh"):
        return False
    return subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0


def _list_with_gh(owner: str) -> list[dict]:
    res = subprocess.run(
        ["gh", "repo", "list", owner, "--limit", "500",
         "--json", "name,updatedAt,description,url"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or "gh repo list failed")
    return [
        {"name": r["name"], "updated_at": r.get("updatedAt"), "description": r.get("description"),
         "clone_url": r["url"] + ".git", "owner": owner}
        for r in json.loads(res.stdout or "[]")
    ]


def _list_with_rest(owner: str) -> list[dict]:
    repos, page = [], 1
    while page <= 5:
        url = f"https://api.github.com/users/{owner}/repos?per_page=100&page={page}"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "ai-workspace"})
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                batch = json.load(resp)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"GitHub API request failed: {exc}")
        repos += [
            {"name": r["name"], "updated_at": r.get("updated_at"), "description": r.get("description"),
             "clone_url": r["clone_url"], "owner": owner}
            for r in batch
        ]
        if len(batch) < 100:
            break
        page += 1
    return repos


def list_repos(sources: list[dict]) -> list[dict]:
    use_gh = _gh_ready()
    if not use_gh:
        print("gh not installed/authenticated: listing public repos only.", file=sys.stderr)
    out: dict[tuple[str, str], dict] = {}
    for s in sources:
        owner = s["owner"]
        for r in (_list_with_gh(owner) if use_gh else _list_with_rest(owner)):
            out[(owner, r["name"])] = r
    return sorted(out.values(), key=lambda r: r["name"].lower())


def clone_repo(repo: dict, target: Path) -> bool:
    extra = ["-c", "core.longpaths=true"] if IS_WINDOWS else []
    if _gh_ready():
        cmd = ["gh", "repo", "clone", f"{repo['owner']}/{repo['name']}", str(target)]
        if IS_WINDOWS:
            cmd += ["--", "--config", "core.longpaths=true"]
    else:
        cmd = ["git", *extra, "clone", repo["clone_url"], str(target)]
    return subprocess.run(cmd).returncode == 0


def run(args) -> int:
    cfg = config.ensure_config()
    repos_dir = Path(args.repos_dir or cfg["repos_dir"]).expanduser()
    sources = [parse_source(s) for s in args.source] if args.source else cfg.get("sources", [])
    if not sources:
        print("No clone sources configured. Add one to the config, e.g.\n"
              f'  {config.config_path()}: "sources": [{{"type": "github", "owner": "<user-or-org>"}}]\n'
              "or pass --source github:<owner>.", file=sys.stderr)
        return 1

    try:
        repos = list_repos(sources)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not repos:
        print("No repositories found.")
        return 0
    repos_dir.mkdir(parents=True, exist_ok=True)
    existing = {r["name"] for r in repos if (repos_dir / r["name"]).exists()}

    if args.all:
        chosen = [r["name"] for r in repos if r["name"] not in existing]
    elif args.select:
        wanted = [n.strip() for n in args.select.split(",") if n.strip()]
        unknown = [n for n in wanted if n not in {r["name"] for r in repos}]
        if unknown:
            print(f"Unknown repositories: {', '.join(unknown)}", file=sys.stderr)
            return 1
        chosen = [n for n in wanted if n not in existing]
    elif sys.stdin.isatty() and sys.stdout.isatty():
        print(f"Loaded {len(repos)} repositories from "
              f"{', '.join(s['owner'] for s in sources)}. Target: {repos_dir}\n")
        chosen = picker.pick(repos, existing)
    else:
        print("Not a terminal: use --select a,b or --all.", file=sys.stderr)
        return 1

    if not chosen:
        print("No repositories selected.")
        return 0
    by_name = {r["name"]: r for r in repos}
    cloned = failed = 0
    for name in chosen:
        print(f"  CLONE {name}...")
        if clone_repo(by_name[name], repos_dir / name):
            cloned += 1
        else:
            print(f"  FAILED {name}", file=sys.stderr)
            failed += 1
    print(f"Done. Cloned: {cloned}, failed: {failed}, already present: {len(existing)}")
    return 1 if failed else 0
