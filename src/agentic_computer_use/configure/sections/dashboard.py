"""Dashboard — currently hardcoded to 127.0.0.1:18790.

This section only shows the current binding and tells the user how to
change it (requires a daemon.py edit + restart, not purely env-driven).
"""
from __future__ import annotations

from ..state import EffectiveConfig
from .. import prompt as P

NAME = "dashboard"
DESCRIPTION = "Dashboard bind address (informational; daemon.py edit required to change)."


def show(eff: EffectiveConfig) -> None:
    P.header(NAME)
    print(f"  {P.bold('bind'):<14} 127.0.0.1:18790  {P.dim('(hardcoded in daemon.py)')}")
    print(f"  {P.bold('URL'):<14} http://127.0.0.1:18790/dashboard")
    print(f"  {P.dim('Remote access: SSH tunnel recommended. See README.md → Remote access.')}")


def interactive(eff: EffectiveConfig, dry_run: bool = False) -> str:
    show(eff)
    return P.yellow(
        "  Dashboard host/port is not env-configurable yet. "
        "Edit daemon.py (web.TCPSite line) if you need to change it."
    )


def apply_kv(eff: EffectiveConfig, pairs: list[str], dry_run: bool = False) -> str:
    return "dashboard section is read-only until daemon.py is refactored."
