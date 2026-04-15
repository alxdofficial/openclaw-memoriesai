"""Thin wrapper over `questionary` with a headless fallback.

Falls back to plain `input()` if questionary isn't installed or stdin isn't
a TTY — that way `detm configure --set key=val` still works in CI/scripts
and the package doesn't hard-require questionary to import.
"""
from __future__ import annotations

import os
import sys
from typing import Sequence

try:
    import questionary            # type: ignore
    _HAS_Q = True
except ImportError:
    _HAS_Q = False

_TTY = sys.stdin.isatty() and sys.stdout.isatty()
_COLOR = _TTY and os.environ.get("NO_COLOR") is None

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text
def bold(s: str)   -> str: return _c("1", s)
def dim(s: str)    -> str: return _c("2", s)
def green(s: str)  -> str: return _c("32", s)
def yellow(s: str) -> str: return _c("33", s)
def red(s: str)    -> str: return _c("31", s)


def can_prompt() -> bool:
    return _TTY


def info(msg: str) -> None:
    print(msg)


def header(section: str) -> None:
    print()
    print(bold(f"─── {section} ───"))


def _fallback_select(msg: str, choices: Sequence[str], default: str | None) -> str:
    print(f"{msg}")
    for i, c in enumerate(choices, 1):
        mark = "*" if c == default else " "
        print(f"  {mark} {i}) {c}")
    while True:
        raw = input(f"[1-{len(choices)}, enter={default or '1'}]: ").strip()
        if not raw:
            return default or choices[0]
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]


def select(msg: str, choices: Sequence[str], default: str | None = None) -> str:
    if _HAS_Q and _TTY:
        return questionary.select(msg, choices=list(choices), default=default).ask() or (default or choices[0])
    return _fallback_select(msg, choices, default)


def text(msg: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    if _HAS_Q and _TTY:
        return questionary.text(msg + hint, default=default).ask() or default
    raw = input(f"{msg}{hint}: ").strip()
    return raw or default


def password(msg: str, default_preview: str = "") -> str:
    hint = f" (current: {default_preview})" if default_preview else ""
    if _HAS_Q and _TTY:
        return questionary.password(msg + hint).ask() or ""
    try:
        import getpass
        return getpass.getpass(f"{msg}{hint}: ")
    except (EOFError, KeyboardInterrupt):
        return ""


def confirm(msg: str, default: bool = False) -> bool:
    if _HAS_Q and _TTY:
        return bool(questionary.confirm(msg, default=default).ask())
    mark = "[Y/n]" if default else "[y/N]"
    raw = input(f"{msg} {mark}: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def redact(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2 + 1:
        return "*" * len(value)
    return f"{value[:keep]}…{value[-keep:]}"
