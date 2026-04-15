"""Services — start / stop / restart / status the five DETM systemd units."""
from __future__ import annotations

import subprocess

from ..state import EffectiveConfig
from .. import prompt as P

NAME = "services"
DESCRIPTION = "Control the five detm-* systemd services."

UNITS = [
    ("detm-daemon",  "DETM HTTP daemon"),
    ("detm-xvfb",    "Xvfb virtual display"),
    ("detm-desktop", "XFCE desktop session"),
    ("detm-vnc",     "x11vnc export"),
    ("detm-novnc",   "noVNC websocket proxy"),
]

ACTIONS = ["status", "restart", "stop", "start", "daemon-reload"]


def show(eff: EffectiveConfig) -> None:
    P.header(NAME)
    for unit, purpose in UNITS:
        active = _is_active(unit + ".service")
        tag = P.green("active") if active else P.red("inactive")
        print(f"  {P.bold(unit):<28} {tag}   {P.dim(purpose)}")


def interactive(eff: EffectiveConfig, dry_run: bool = False) -> str:
    show(eff)
    action = P.select("Action?", ACTIONS, default="status")
    if action == "status":
        return subprocess.run(
            ["systemctl", "status", "--no-pager", *[u for u, _ in UNITS]],
        ).returncode and "" or ""

    if action == "daemon-reload":
        if dry_run:
            return "DRY RUN — would run: sudo systemctl daemon-reload"
        r = subprocess.run(["sudo", "-n", "systemctl", "daemon-reload"])
        return P.green("✓ reloaded") if r.returncode == 0 else P.yellow("sudo required — run: sudo systemctl daemon-reload")

    choice = P.select("Which unit?", [u for u, _ in UNITS] + ["all"], default="all")
    targets = [u for u, _ in UNITS] if choice == "all" else [choice]
    cmd = ["sudo", "-n", "systemctl", action, *targets]
    if dry_run:
        return "DRY RUN — would run: " + " ".join(cmd)
    r = subprocess.run(cmd)
    return P.green(f"✓ {action} {targets}") if r.returncode == 0 else P.yellow(
        "sudo not available; run: " + " ".join(cmd[1:])
    )


def apply_kv(eff: EffectiveConfig, pairs: list[str], dry_run: bool = False) -> str:
    return "services section takes no --set pairs; use `detm configure services` interactively."


def _is_active(unit: str) -> bool:
    try:
        r = subprocess.run(["systemctl", "is-active", unit],
                           capture_output=True, text=True, timeout=3)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
