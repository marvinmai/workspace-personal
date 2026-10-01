"""`doctor`: report what is installed and which repos drifted (read-only)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import config, link, shell

IS_WINDOWS = sys.platform.startswith("win")
OK, WARN, FAIL = "OK", "WARN", "FAIL"


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    return res.stdout.decode("utf-8", "replace") if res.returncode == 0 else ""


def check_tools() -> list[tuple[str, str, str]]:
    out = [(OK, "python", sys.version.split()[0])]
    for tool, level in (("git", FAIL), ("claude", WARN), ("gh", WARN), ("node", WARN), ("pwsh", WARN)):
        path = shutil.which(tool)
        out.append((OK, tool, path) if path else (level, tool, "not found" + (
            " (only needed for Windows profile helpers)" if tool == "pwsh" and not IS_WINDOWS else "")))
    py_hook = "python3" if not IS_WINDOWS else "python3 (hooks use this name; if missing, switch to `py -3`)"
    if not shutil.which("python3"):
        out.append((FAIL if not IS_WINDOWS else WARN, "hooks interpreter", f"'{py_hook}' not on PATH"))
    return out


def check_config() -> tuple[list[tuple[str, str, str]], dict]:
    cfg = config.load_config()
    res = [(OK if config.config_path().is_file() else WARN, "config", str(config.config_path()))]
    for key in ("repos_dir", "ai_personal_dir", "notes_dir"):
        p = Path(cfg[key]).expanduser()
        res.append((OK if p.is_dir() else FAIL, key, str(p)))
    if not cfg.get("sources"):
        res.append((WARN, "sources", "none configured: `clone` needs github:<owner>"))
    return res, cfg


def check_link(cfg: dict) -> list[tuple[str, str, str]]:
    res = []
    ai_dir = Path(cfg["ai_personal_dir"]).expanduser()
    if shutil.which("claude"):
        listed = subprocess.run(["claude", "plugin", "list"], capture_output=True, text=True).stdout
        res.append((OK, "plugin", "personal@ai-personal installed") if "personal@ai-personal" in listed
                   else (WARN, "plugin", "personal@ai-personal not installed: run `link`"))
    rules_dir = link.claude_dir() / "rules"
    for src in sorted((ai_dir / "rules").glob("*.md")):
        dest = rules_dir / src.name
        want = link.RULE_MARK + "\n" + src.read_text(encoding="utf-8") if src.exists() else None
        res.append((OK, f"rule {src.name}", "deployed") if dest.exists() and dest.read_text(encoding="utf-8") == want
                   else (WARN, f"rule {src.name}", "missing or outdated: run `link`"))
    sp = link.claude_dir() / "settings.json"
    frag = ai_dir / "settings" / "permissions.fragment.json"
    if frag.is_file():
        settings = json.loads(sp.read_text(encoding="utf-8-sig")) if sp.is_file() else {}
        pending = link.merge_permissions(settings, json.loads(frag.read_text(encoding="utf-8")))
        res.append((WARN, "settings", "permissions fragment not merged: run `link`") if pending
                   else (OK, "settings", "permissions merged"))
    if not IS_WINDOWS:
        has = any(shell.BEGIN in p.read_text(encoding="utf-8")
                  for p in (Path.home() / ".bashrc", Path.home() / ".zshrc") if p.exists())
        res.append((OK, "shell helpers", "present") if has else (WARN, "shell helpers", "missing: run `link`"))
    return res


def check_repo(repo: Path) -> list[tuple[str, str, str]]:
    if not (repo / ".git").exists():
        return [(WARN, repo.name, "not a git repo")]
    res = []
    # CRLF in tracked scripts breaks shebangs on Linux/WSL.
    crlf = []
    for f in _git(repo, "ls-files", "*.sh", "*.py", "*.mjs").split("\n"):
        if f and (repo / f).is_file() and b"\r\n" in (repo / f).read_bytes()[:4096] and not IS_WINDOWS:
            crlf.append(f)
    res.append((WARN, f"{repo.name} line endings", f"CRLF in {len(crlf)} script(s): {', '.join(crlf[:3])}")
               if crlf else (OK, f"{repo.name} line endings", "LF"))
    # Scripts with a shebang need the exec bit in git to survive a Windows round trip.
    noexec = []
    for line in _git(repo, "ls-files", "-s").split("\n"):
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[0] == "100644" and parts[3].endswith((".sh", ".py")):
            try:
                if (repo / parts[3]).read_bytes()[:2] == b"#!":
                    noexec.append(parts[3])
            except OSError:
                pass
    res.append((WARN, f"{repo.name} exec bits", f"{len(noexec)} script(s) with shebang but mode 644: {', '.join(noexec[:3])}")
               if noexec else (OK, f"{repo.name} exec bits", "ok"))
    deny = repo / ".denylist"
    own_check = repo / "scripts" / "check-confidentiality.sh"
    if deny.is_file() and own_check.is_file() and not IS_WINDOWS:
        # The repo defines its own (scoped) check, e.g. the notes vault exempts its career portfolio.
        ok = subprocess.run(["sh", str(own_check)], capture_output=True).returncode == 0
        res.append((OK, f"{repo.name} denylist", "clean (repo's own check)") if ok
                   else (FAIL, f"{repo.name} denylist", "scripts/check-confidentiality.sh failed"))
    elif deny.is_file():
        terms = [t for t in deny.read_text(encoding="utf-8").splitlines() if t.strip() and not t.startswith("#")]
        if terms:
            rx = re.compile("|".join(terms), re.I)
            hits = [f for f in _git(repo, "ls-files").split("\n")
                    if f and not f.startswith(".denylist") and (repo / f).is_file()
                    and rx.search(f + "\n" + (repo / f).read_text(encoding="utf-8", errors="ignore"))]
            res.append((FAIL, f"{repo.name} denylist", f"{len(hits)} file(s): {', '.join(hits[:3])}")
                       if hits else (OK, f"{repo.name} denylist", "clean"))
    else:
        res.append((WARN, f"{repo.name} denylist", "no .denylist (copy .denylist.example)"))
    return res


def run(as_json: bool = False) -> int:
    rows = check_tools()
    cfg_rows, cfg = check_config()
    rows += cfg_rows
    rows += check_link(cfg) if Path(cfg["ai_personal_dir"]).expanduser().is_dir() else []
    for key in ("ai_personal_dir", "notes_dir"):
        p = Path(cfg[key]).expanduser()
        if p.is_dir():
            rows += check_repo(p)
    rows += check_repo(config.WORKSPACE_ROOT)
    if as_json:
        print(json.dumps([{"level": l, "check": c, "detail": d} for l, c, d in rows], indent=2))
    else:
        for level, check, detail in rows:
            print(f"{level:<5} {check:<28} {detail}")
    return 1 if any(l == FAIL for l, _, _ in rows) else 0
