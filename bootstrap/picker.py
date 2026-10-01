"""Interactive live-search multi-select (arrow keys, space, enter).

Port of the former PowerShell repo picker. Filtering is pure and unit-tested;
terminal handling is isolated in read_key().
"""
from __future__ import annotations

import shutil
import sys


def filter_repos(repos: list[dict], text: str) -> list[dict]:
    """Comma-separated terms are ANDed (case-insensitive substring on the name).

    Names starting with the first term come first, then the other matches.
    """
    terms = [t.strip().lower() for t in text.split(",") if t.strip()]
    if not terms:
        return list(repos)
    prefix, other = [], []
    for r in repos:
        name = r["name"].lower()
        if all(t in name for t in terms):
            (prefix if name.startswith(terms[0]) else other).append(r)
    return prefix + other


# --- terminal input ----------------------------------------------------------

def _read_key_windows() -> str:
    import msvcrt

    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "")
    return _normalize(ch)


def _read_key_posix() -> str:
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            # Arrow keys arrive as ESC [ A/B; a lone ESC is "quit".
            import select

            if select.select([sys.stdin], [], [], 0.05)[0]:
                seq = sys.stdin.read(2)
                return {"[A": "up", "[B": "down"}.get(seq, "")
            return "esc"
        return _normalize(ch)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _normalize(ch: str) -> str:
    if ch in ("\r", "\n"):
        return "enter"
    if ch == "\t":
        return "tab"
    if ch in ("\x7f", "\b"):
        return "backspace"
    if ch == "\x1b":
        return "esc"
    if ch == "\x03":
        raise KeyboardInterrupt
    return ch


def read_key() -> str:
    return _read_key_windows() if sys.platform.startswith("win") else _read_key_posix()


# --- UI ----------------------------------------------------------------------

def pick(repos: list[dict], existing: set[str]) -> list[str]:
    """Return the selected repo names ([] if cancelled)."""
    if sys.platform.startswith("win"):
        import os

        os.system("")  # enable ANSI escapes on Windows 10+ consoles

    search, mode = "", "search"
    cursor = scroll = 0
    selected: set[str] = set()
    rows = max(3, min(20, shutil.get_terminal_size().lines - 8))
    drawn = 0
    out = sys.stdout

    while True:
        width = max(40, shutil.get_terminal_size().columns - 1)
        filtered = filter_repos(repos, search)
        count = len(filtered)
        cursor = max(0, min(cursor, count - 1)) if count else 0
        if scroll > cursor:
            scroll = cursor
        if cursor >= scroll + rows:
            scroll = cursor - rows + 1

        lines = [
            ("  Search: " + search + ("_" if mode == "search" else "  (Tab to edit)")),
            f"  {count} matches | Selected: {len(selected)}",
            "",
        ]
        for i in range(rows):
            idx = scroll + i
            if idx < count:
                r = filtered[idx]
                name = r["name"]
                mark = "[E]" if name in existing else ("[X]" if name in selected else "[ ]")
                ptr = ">" if (mode == "select" and idx == cursor) else " "
                lines.append(f" {ptr} {mark} {name[:45]:<45} {(r.get('updated_at') or '')[:10]}")
            else:
                lines.append("")
        lines.append(
            " Type to filter (comma=AND) | Tab=navigate | Enter=confirm | Esc=quit"
            if mode == "search"
            else " Up/Down=move | Space=toggle | a=all | n=none | Tab=search | Enter=confirm | Esc=quit"
        )

        if drawn:
            out.write(f"\x1b[{drawn}A")
        for line in lines:
            out.write("\x1b[2K" + line[:width] + "\n")
        out.flush()
        drawn = len(lines)

        key = read_key()
        if key == "esc":
            return []
        if key == "enter":
            return sorted(selected)
        if key == "tab":
            mode = "select" if mode == "search" else "search"
            if mode == "select":
                cursor = scroll
            continue
        if mode == "search":
            if key == "backspace":
                search = search[:-1]
                cursor = scroll = 0
            elif len(key) == 1 and key >= " ":
                search += key
                cursor = scroll = 0
        else:
            if key == "up":
                cursor = max(0, cursor - 1)
            elif key == "down":
                cursor = min(count - 1, cursor + 1)
            elif key == " " and count:
                name = filtered[cursor]["name"]
                if name not in existing:
                    selected ^= {name}
            elif key.lower() == "a":
                selected |= {r["name"] for r in filtered if r["name"] not in existing}
            elif key.lower() == "n":
                selected.clear()
