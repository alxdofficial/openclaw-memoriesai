"""MCP registration in openclaw.json.

Register (install), update (re-point to a new venv/cwd), or remove the
`agentic-computer-use` MCP server entry. Keys (OPENROUTER_API_KEY etc.)
are handled by the `keys` section — this section handles the *plumbing*.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..state import EffectiveConfig
from .. import io as cfgio
from .. import prompt as P

NAME = "mcp"
DESCRIPTION = "MCP server registration in ~/.openclaw/openclaw.json."

ACTIONS = ["show", "install/update", "remove"]


def show(eff: EffectiveConfig) -> None:
    P.header(NAME)
    entry = eff.mcp_entry()
    if not entry:
        print(P.yellow("  not registered"))
        return
    for key in ("command", "args", "cwd", "env"):
        val = entry.get(key, "")
        if key == "env" and isinstance(val, dict):
            print(f"  {P.bold(key):<14}")
            for k, v in val.items():
                redacted = P.redact(v) if any(t in k.lower() for t in ("key", "token")) else v
                print(f"    {k}={redacted}")
        else:
            print(f"  {P.bold(key):<14} {val}")


def interactive(eff: EffectiveConfig, dry_run: bool = False) -> str:
    show(eff)
    action = P.select("Action?", ACTIONS, default="install/update")
    if action == "show":
        return ""

    if action == "remove":
        if not P.confirm("Remove MCP registration? Daemon keeps running.", default=False):
            return "  (cancelled)"
        _cfg, diff = cfgio.update_openclaw_config(_remove_mcp, dry_run=dry_run)
        return (diff or "  (nothing to remove)")

    # install/update
    repo = Path(__file__).resolve().parents[4]  # src/agentic_computer_use/configure/sections → repo
    current = eff.mcp_entry()
    cwd  = P.text("Repo dir (cwd)?",     default=current.get("cwd")     or str(repo))
    venv = P.text("Python interpreter?", default=current.get("command") or f"{repo}/.venv/bin/python3")
    args = current.get("args") or ["-m", "agentic_computer_use.server"]

    def patch(c: dict[str, Any]) -> dict[str, Any]:
        c.setdefault("mcp", {}).setdefault("servers", {})
        entry = c["mcp"]["servers"].setdefault("agentic-computer-use", {})
        entry["command"] = venv
        entry["args"] = args
        entry["cwd"] = cwd
        env = entry.setdefault("env", {})
        env.setdefault("DISPLAY", eff.get("DISPLAY")[0] or ":99")
        env.setdefault("PYTHONPATH", f"{cwd}/src")
        # OpenRouter key: only copy if present in unit env
        unit_key = eff.unit_env.get("OPENROUTER_API_KEY")
        if unit_key and "OPENROUTER_API_KEY" not in env:
            env["OPENROUTER_API_KEY"] = unit_key
        return c

    _cfg, diff = cfgio.update_openclaw_config(patch, dry_run=dry_run)
    return diff or "  (already up to date)"


def apply_kv(eff: EffectiveConfig, pairs: list[str], dry_run: bool = False) -> str:
    return "mcp section takes no --set pairs; use `detm configure mcp` interactively."


def _remove_mcp(cfg: dict[str, Any]) -> dict[str, Any]:
    cfg.get("mcp", {}).get("servers", {}).pop("agentic-computer-use", None)
    return cfg
