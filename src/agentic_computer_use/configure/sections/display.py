"""Display — which X display the daemon binds to + default task dims.

Changing the Xvfb resolution itself requires editing `/etc/systemd/system/
detm-xvfb.service` (ExecStart `-screen 0 WxHxD`). We surface that as a
manual step — the override is shown but not applied without sudo.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..state import EffectiveConfig
from .. import prompt as P
from ._common import show_env_section, apply_env_updates, parse_kv

NAME = "display"
DESCRIPTION = "X display and default task window dimensions."

XVFB_UNIT = Path("/etc/systemd/system/detm-xvfb.service")


def show(eff: EffectiveConfig) -> None:
    show_env_section(NAME, eff)
    if XVFB_UNIT.exists():
        xvfb_execstart = _current_xvfb_execstart()
        print(f"  {P.dim('detm-xvfb ExecStart:')} {xvfb_execstart}")


def interactive(eff: EffectiveConfig, dry_run: bool = False) -> str:
    show(eff)
    updates: dict[str, str | None] = {}
    updates["DISPLAY"] = P.text("X display?", default=eff.get("DISPLAY")[0] or ":99")
    updates["ACU_TASK_DISPLAY_WIDTH"]  = P.text(
        "Default task display width?",  default=eff.get("ACU_TASK_DISPLAY_WIDTH")[0] or "1280")
    updates["ACU_TASK_DISPLAY_HEIGHT"] = P.text(
        "Default task display height?", default=eff.get("ACU_TASK_DISPLAY_HEIGHT")[0] or "720")

    summary = apply_env_updates(updates, dry_run=dry_run)

    if P.confirm("Also change the Xvfb screen size (requires sudo + Xvfb restart)?",
                 default=False):
        w = P.text("Xvfb screen width?",  default="1920")
        h = P.text("Xvfb screen height?", default="1080")
        d = P.text("Depth?",               default="24")
        summary += "\n" + _rewrite_xvfb_screen(w, h, d, dry_run=dry_run)
    return summary


def apply_kv(eff: EffectiveConfig, pairs: list[str], dry_run: bool = False) -> str:
    return apply_env_updates(dict(parse_kv(pairs)), dry_run=dry_run)


def _current_xvfb_execstart() -> str:
    try:
        for line in XVFB_UNIT.read_text().splitlines():
            s = line.strip()
            if s.startswith("ExecStart="):
                return s[len("ExecStart="):]
    except OSError:
        pass
    return "(not readable)"


def _rewrite_xvfb_screen(w: str, h: str, d: str, *, dry_run: bool) -> str:
    if not XVFB_UNIT.exists():
        return P.yellow("  detm-xvfb.service not installed — skipping")
    text = XVFB_UNIT.read_text()
    new_lines: list[str] = []
    changed = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("ExecStart="):
            parts = s[len("ExecStart="):].split()
            for i, tok in enumerate(parts):
                if tok == "0" and i > 0 and parts[i - 1] == "-screen":
                    if i + 1 < len(parts):
                        parts[i + 1] = f"{w}x{h}x{d}"
                        changed = True
                        break
            new_lines.append("ExecStart=" + " ".join(parts))
        else:
            new_lines.append(line)
    if not changed:
        return P.yellow("  Could not find Xvfb -screen 0 WxHxD line — skipping")
    new_text = "\n".join(new_lines) + "\n"
    if dry_run:
        return "DRY RUN — new detm-xvfb ExecStart:\n  " + [l for l in new_lines if l.startswith("ExecStart=")][0]
    r = subprocess.run(["sudo", "-n", "tee", str(XVFB_UNIT)],
                       input=new_text, text=True, capture_output=True)
    if r.returncode != 0:
        return (P.yellow("  sudo tee failed — run manually:") +
                f"\n    echo {new_text!r} | sudo tee {XVFB_UNIT}" +
                "\n    sudo systemctl daemon-reload && sudo systemctl restart detm-xvfb detm-desktop")
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=False)
    subprocess.run(["sudo", "systemctl", "restart", "detm-xvfb", "detm-desktop"], check=False)
    return P.green("✓ Xvfb resolution updated and display restarted")
