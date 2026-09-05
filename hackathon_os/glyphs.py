"""Terminal glyphs and colour, chosen for what the console can actually print.

A Windows console defaults to cp1252, which cannot encode ✓ ○ ━ █. Emitting
them raises UnicodeEncodeError and takes the whole command down, so we probe
the real stdout encoding once and fall back to ASCII when it will not fit.
"""

from __future__ import annotations

import os
import sys

UNICODE = {
    "ok": "✓", "fail": "✗", "pending": "○", "blocked": "⚠", "skip": "–",
    "human": "?", "bullet": "•", "bar_full": "█", "bar_empty": "░",
    "rule": "━", "arrow": "→", "unreachable": "⊘",
}
ASCII = {
    "ok": "+", "fail": "x", "pending": ".", "blocked": "!", "skip": "-",
    "human": "?", "bullet": "*", "bar_full": "#", "bar_empty": ".",
    "rule": "=", "arrow": "->", "unreachable": "/",
}


def _console_handles_unicode() -> bool:
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "".join(UNICODE.values()).encode(enc)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _init() -> dict[str, str]:
    # Prefer upgrading the stream to UTF-8; most modern Windows terminals
    # handle it, and it keeps the nicer glyphs.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError, OSError):
        pass
    return UNICODE if _console_handles_unicode() else ASCII


G: dict[str, str] = _init()


def _colour_ok() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    return True


_C = _colour_ok()


def _c(code: str) -> str:
    return code if _C else ""


GREY = _c("\033[90m")
BLUE = _c("\033[34m")
GREEN = _c("\033[32m")
RED = _c("\033[31m")
YELLOW = _c("\033[33m")
BOLD = _c("\033[1m")
RESET = _c("\033[0m")


def rule(width: int = 52) -> str:
    return G["rule"] * width


def bar(fraction: float, width: int = 20) -> str:
    filled = int(round(max(0.0, min(1.0, fraction)) * width))
    return G["bar_full"] * filled + G["bar_empty"] * (width - filled)


def say(text: str = "") -> None:
    """Print without ever dying on an un-encodable character."""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"))
