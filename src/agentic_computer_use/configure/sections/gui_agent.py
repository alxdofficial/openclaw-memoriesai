"""GUI agent backend — what does the cursor-placement work."""
from __future__ import annotations

from ..state import EffectiveConfig
from .. import prompt as P
from ._common import show_env_section, apply_env_updates, parse_kv

NAME = "gui-agent"
DESCRIPTION = "Which model places the cursor for gui_agent."

BACKENDS = ["direct", "uitars", "claude_cu", "omniparser"]


def show(eff: EffectiveConfig) -> None:
    show_env_section(NAME, eff)


def interactive(eff: EffectiveConfig, dry_run: bool = False) -> str:
    show(eff)
    current, _ = eff.get("ACU_GUI_AGENT_BACKEND")
    choice = P.select("GUI agent backend?", BACKENDS, default=current or "uitars")
    updates: dict[str, str | None] = {"ACU_GUI_AGENT_BACKEND": choice}

    # Live_ui / gui_agent reasoning model — used by all backends
    live_model = eff.get("ACU_OPENROUTER_LIVE_MODEL")[0]
    updates["ACU_OPENROUTER_LIVE_MODEL"] = P.text(
        "OpenRouter model for gui_agent reasoning?",
        default=live_model or "google/gemini-3-flash-preview",
    )

    if choice == "uitars":
        updates["ACU_UITARS_OPENROUTER_MODEL"] = P.text(
            "UI-TARS grounding model (OpenRouter)?",
            default=eff.get("ACU_UITARS_OPENROUTER_MODEL")[0] or "bytedance/ui-tars-1.5-7b",
        )
        if P.confirm("Also configure local Ollama fallback?", default=False):
            updates["ACU_UITARS_OLLAMA_MODEL"] = P.text(
                "Ollama model?",
                default=eff.get("ACU_UITARS_OLLAMA_MODEL")[0] or "0000/ui-tars-1.5-7b",
            )

    return apply_env_updates(updates, dry_run=dry_run)


def apply_kv(eff: EffectiveConfig, pairs: list[str], dry_run: bool = False) -> str:
    return apply_env_updates(dict(parse_kv(pairs)), dry_run=dry_run)
