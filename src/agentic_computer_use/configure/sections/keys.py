"""API keys — OpenRouter / Anthropic / MAVI.

Keys are stored in *both* the systemd unit (so the daemon sees them) and
`openclaw.json` under the MCP server entry (so the MCP-spawned server
process sees them). `detm configure keys` keeps both in sync.
"""
from __future__ import annotations

from typing import Any

from ..state import EffectiveConfig, SECTION_ENV
from .. import io as cfgio
from .. import prompt as P
from ._common import show_env_section, apply_env_updates, parse_kv

NAME = "keys"
DESCRIPTION = "API keys for OpenRouter / Anthropic / MAVI."

MCP_SYNC_KEYS = {"OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "MAVI_API_KEY"}


def show(eff: EffectiveConfig) -> None:
    show_env_section(NAME, eff)
    mcp_env = eff.mcp_entry().get("env", {}) if eff.mcp_entry() else {}
    if mcp_env:
        print(f"\n  {P.dim('openclaw.json MCP env (read-only here):')}")
        for k in sorted(MCP_SYNC_KEYS):
            if k in mcp_env:
                print(f"    {k:<24} {P.redact(mcp_env[k])}")


def interactive(eff: EffectiveConfig, dry_run: bool = False) -> str:
    show(eff)
    updates: dict[str, str | None] = {}

    for env_key, desc in SECTION_ENV[NAME]:
        current, src = eff.get(env_key)
        msg = f"{desc} ({env_key})"
        if current:
            keep = P.confirm(f"Keep existing {env_key} ({P.redact(current)})?", default=True)
            if keep:
                continue
        new_val = P.password(f"New value for {env_key} (blank = clear)", P.redact(current))
        if new_val == "" and current:
            if P.confirm(f"Clear {env_key}?", default=False):
                updates[env_key] = None
        elif new_val and new_val != current:
            updates[env_key] = new_val

    if not updates:
        return "  (no key changes requested)"

    summary = apply_env_updates(updates, dry_run=dry_run)

    # Sync the same keys into openclaw.json mcp env
    sync_keys = {k: v for k, v in updates.items() if k in MCP_SYNC_KEYS}
    if sync_keys:
        _new, diff = cfgio.update_openclaw_config(
            lambda c: _patch_mcp_env(c, sync_keys),
            dry_run=dry_run,
        )
        if diff:
            summary += "\n" + P.green("openclaw.json mcp env synced:") + "\n  " + diff
    return summary


def apply_kv(eff: EffectiveConfig, pairs: list[str], dry_run: bool = False) -> str:
    updates: dict[str, str | None] = dict(parse_kv(pairs))
    summary = apply_env_updates(updates, dry_run=dry_run)
    sync_keys = {k: v for k, v in updates.items() if k in MCP_SYNC_KEYS}
    if sync_keys:
        _new, diff = cfgio.update_openclaw_config(
            lambda c: _patch_mcp_env(c, sync_keys), dry_run=dry_run,
        )
        if diff:
            summary += "\n" + diff
    return summary


def _patch_mcp_env(cfg: dict[str, Any], updates: dict[str, str | None]) -> dict[str, Any]:
    cfg.setdefault("mcp", {}).setdefault("servers", {})
    entry = cfg["mcp"]["servers"].setdefault("agentic-computer-use", {})
    env = entry.setdefault("env", {})
    for k, v in updates.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return cfg
