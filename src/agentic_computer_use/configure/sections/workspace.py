"""Workspace and data directories."""
from __future__ import annotations

from ..state import EffectiveConfig
from .. import prompt as P
from ._common import show_env_section, apply_env_updates, parse_kv

NAME = "workspace"
DESCRIPTION = "ACU_WORKSPACE (skills/memory) and ACU_DATA_DIR (DB + screenshots)."


def show(eff: EffectiveConfig) -> None:
    show_env_section(NAME, eff)


def interactive(eff: EffectiveConfig, dry_run: bool = False) -> str:
    show(eff)
    updates: dict[str, str | None] = {
        "ACU_WORKSPACE": P.text("Workspace dir?",
                                default=eff.get("ACU_WORKSPACE")[0] or "/home/alex/.openclaw/workspace"),
        "ACU_DATA_DIR":  P.text("Data dir?",
                                default=eff.get("ACU_DATA_DIR")[0] or "/home/alex/.agentic-computer-use"),
    }
    return apply_env_updates(updates, dry_run=dry_run)


def apply_kv(eff: EffectiveConfig, pairs: list[str], dry_run: bool = False) -> str:
    return apply_env_updates(dict(parse_kv(pairs)), dry_run=dry_run)
