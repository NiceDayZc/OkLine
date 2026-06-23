"""Tiny terminal-UI toolkit — soft colours, boxes, tables, prompts.

Self-contained (no dependencies). Colours are a deliberately *muted* palette
(slate / sage / grey / soft amber — no harsh red/yellow/magenta) and are turned
off automatically when output is not a TTY, when ``NO_COLOR`` is set, or on a
``dumb`` terminal.  Used by :mod:`okline.menu`.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Sequence

from ._util import reconfigure_stdout_utf8


def _enable_windows_vt() -> None:
    if os.name != "nt":
        return
    try:  # turn on ANSI escape processing on Windows 10+ consoles
        import ctypes

        k = ctypes.windll.kernel32
        k.SetConsoleMode(k.GetStdHandle(-11), 7)
    except Exception:  # pragma: no cover
        pass


_enable_windows_vt()
reconfigure_stdout_utf8()  # box-drawing glyphs + Thai + emoji on any code page

ENABLED = (
    hasattr(sys.stdout, "isatty")
    and sys.stdout.isatty()
    and os.environ.get("NO_COLOR") is None
    and os.environ.get("TERM") != "dumb"
)

# Can we safely print Unicode box-drawing glyphs? (else fall back to ASCII)
_UNICODE = "utf" in (getattr(sys.stdout, "encoding", "") or "").lower()
if _UNICODE:
    _BOX = {
        "tl": "╭",
        "tr": "╮",
        "bl": "╰",
        "br": "╯",
        "h": "─",
        "v": "│",
        "ml": "├",
        "mr": "┤",
    }
    GLYPH = {"arrow": "›", "check": "✓", "bullet": "•", "ell": "…"}
else:
    _BOX = {
        "tl": "+",
        "tr": "+",
        "bl": "+",
        "br": "+",
        "h": "-",
        "v": "|",
        "ml": "+",
        "mr": "+",
    }
    GLYPH = {"arrow": ">", "check": "*", "bullet": "*", "ell": "..."}

# muted 256-colour palette — easy on the eyes
_C = {
    "title": "38;5;110",  # soft slate blue
    "accent": "38;5;108",  # sage green
    "key": "38;5;152",  # pale cyan (menu numbers)
    "dim": "38;5;245",  # grey
    "warn": "38;5;179",  # soft amber
    "border": "38;5;240",  # dark grey
}


def _wrap(s: str, code: str) -> str:
    return f"\033[{code}m{s}\033[0m" if ENABLED else s


def title(s: str) -> str:
    return _wrap(s, _C["title"])


def accent(s: str) -> str:
    return _wrap(s, _C["accent"])


def key(s: str) -> str:
    return _wrap(s, _C["key"])


def dim(s: str) -> str:
    return _wrap(s, _C["dim"])


def warn(s: str) -> str:
    return _wrap(s, _C["warn"])


def bold(s: str) -> str:
    return _wrap(s, "1")


def clear() -> None:
    if ENABLED:
        sys.stdout.write("\033[2J\033[3J\033[H")
        sys.stdout.flush()
    else:
        print()


_W = 60  # content width


def _border(s: str) -> str:
    return _wrap(s, _C["border"])


def panel(lines: Sequence[str], *, head: str = "") -> None:
    """Draw a rounded box around ``lines`` (already-styled strings ok)."""
    h, v = _BOX["h"], _BOX["v"]
    top = _BOX["tl"] + h * (_W - 2) + _BOX["tr"]
    bot = _BOX["bl"] + h * (_W - 2) + _BOX["br"]
    print(_border(top))
    if head:
        print(_border(v + " ") + title(head.ljust(_W - 4)) + _border(" " + v))
        print(_border(_BOX["ml"] + h * (_W - 2) + _BOX["mr"]))
    for ln in lines:
        # pad on the *visible* length (strip ANSI for width math)
        pad = " " * max(0, _W - 4 - _visible_len(ln))
        print(_border(v + " ") + ln + pad + _border(" " + v))
    print(_border(bot))


def _visible_len(s: str) -> int:
    out, i = 0, 0
    while i < len(s):
        if s[i] == "\033":
            j = s.find("m", i)
            i = j + 1 if j != -1 else i + 1
            continue
        out += 1
        i += 1
    return out


def rule(label: str = "") -> None:
    h = _BOX["h"]
    if label:
        print(dim(h * 2 + " " + label + " " + h * max(0, _W - 4 - len(label))))
    else:
        print(dim(h * _W))


def menu(items: Sequence[str], *, quit_label: str = "Quit") -> None:
    for i, label in enumerate(items, 1):
        print(f"  {key(f'{i:>2}')}  {label}")
    print(f"  {key(' 0')}  {dim(quit_label)}")


def table(rows: Iterable[Sequence[str]], *, widths: Sequence[int] = ()) -> None:
    rows = [list(map(str, r)) for r in rows]
    if not rows:
        print(dim("  (none)"))
        return
    ncol = max(len(r) for r in rows)
    w = list(widths) + [0] * (ncol - len(widths))
    for r in rows:
        for i, cell in enumerate(r):
            w[i] = max(w[i], _visible_len(cell))
    for r in rows:
        print("  " + "  ".join(c.ljust(w[i]) for i, c in enumerate(r)))


def prompt(label: str, default: str = "") -> str:
    suffix = dim(f" [{default}]") if default else ""
    try:
        v = input(accent(GLYPH["arrow"]) + " " + label + suffix + dim(": ")).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return v or default


def pause() -> None:
    try:
        input("\n" + dim("  press Enter to continue" + GLYPH["ell"]))
    except (EOFError, KeyboardInterrupt):
        pass


def screen(heading: str, lines: list[str] | None = None) -> None:
    """Clear and draw a titled screen header."""
    clear()
    print()
    panel(lines or [], head=heading)
    print()


def ok(s: str) -> str:
    return accent(GLYPH["check"] + " ") + s


def info(s: str) -> str:
    return dim(GLYPH["bullet"] + " ") + s
