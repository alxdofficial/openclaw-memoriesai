"""Runtime knobs — debug, stuck detection, desktop_look compression, storage cap."""
from __future__ import annotations

from ..state import EffectiveConfig
from .. import prompt as P
from ._common import show_env_section, apply_env_updates, parse_kv

NAME = "runtime"
DESCRIPTION = "Debug logging, stuck detection, desktop_look quality, storage cap."


def show(eff: EffectiveConfig) -> None:
    show_env_section(NAME, eff)


def interactive(eff: EffectiveConfig, dry_run: bool = False) -> str:
    show(eff)
    updates: dict[str, str | None] = {}
    updates["ACU_DEBUG"]                = "1" if P.confirm("Enable debug logging?",
                                           default=eff.get("ACU_DEBUG")[0] == "1") else "0"
    updates["ACU_STUCK_DETECTION"]      = "1" if P.confirm("Enable stuck-task detection?",
                                           default=eff.get("ACU_STUCK_DETECTION")[0] == "1") else "0"
    updates["ACU_DESKTOP_LOOK_DIM"]     = P.text("desktop_look max dim (px)?",
                                          default=eff.get("ACU_DESKTOP_LOOK_DIM")[0] or "1200")
    updates["ACU_DESKTOP_LOOK_QUALITY"] = P.text("desktop_look JPEG quality (1-100)?",
                                          default=eff.get("ACU_DESKTOP_LOOK_QUALITY")[0] or "72")
    updates["ACU_MAX_RECORDINGS_MB"]    = P.text("Recordings cap (MB, 0=unlimited)?",
                                          default=eff.get("ACU_MAX_RECORDINGS_MB")[0] or "1000")
    return apply_env_updates(updates, dry_run=dry_run)


def apply_kv(eff: EffectiveConfig, pairs: list[str], dry_run: bool = False) -> str:
    return apply_env_updates(dict(parse_kv(pairs)), dry_run=dry_run)
