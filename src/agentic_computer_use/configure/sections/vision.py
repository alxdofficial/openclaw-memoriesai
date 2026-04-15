"""Vision backend — what model answers YES/NO for smart_wait."""
from __future__ import annotations

from ..state import EffectiveConfig
from .. import prompt as P
from ._common import show_env_section, apply_env_updates, parse_kv

NAME = "vision"
DESCRIPTION = "Which model evaluates screenshots for smart_wait."

BACKENDS = ["openrouter", "ollama", "vllm", "claude", "passthrough"]


def show(eff: EffectiveConfig) -> None:
    show_env_section(NAME, eff)


def interactive(eff: EffectiveConfig, dry_run: bool = False) -> str:
    show(eff)
    current_backend, _ = eff.get("ACU_VISION_BACKEND")
    choice = P.select("Vision backend?", BACKENDS, default=current_backend or "openrouter")

    updates: dict[str, str | None] = {"ACU_VISION_BACKEND": choice}

    if choice == "openrouter":
        current_model, _ = eff.get("ACU_OPENROUTER_VISION_MODEL")
        new_model = P.text("OpenRouter vision model?",
                           default=current_model or "google/gemini-2.0-flash-lite-001")
        updates["ACU_OPENROUTER_VISION_MODEL"] = new_model
    elif choice == "ollama":
        current_model, _ = eff.get("ACU_VISION_MODEL")
        new_model = P.text("Ollama model name?", default=current_model or "minicpm-v")
        updates["ACU_VISION_MODEL"] = new_model
    elif choice == "vllm":
        updates["ACU_VLLM_URL"]   = P.text("vLLM URL?",   default=eff.get("ACU_VLLM_URL")[0] or "http://localhost:8000")
        updates["ACU_VLLM_MODEL"] = P.text("vLLM model?", default=eff.get("ACU_VLLM_MODEL")[0] or "ui-tars-1.5-7b")
    elif choice == "claude":
        updates["ACU_CLAUDE_VISION_MODEL"] = P.text(
            "Claude vision model?",
            default=eff.get("ACU_CLAUDE_VISION_MODEL")[0] or "claude-sonnet-4-20250514",
        )

    return apply_env_updates(updates, dry_run=dry_run)


def apply_kv(eff: EffectiveConfig, pairs: list[str], dry_run: bool = False) -> str:
    updates: dict[str, str | None] = {k: v for k, v in parse_kv(pairs).items()}
    return apply_env_updates(updates, dry_run=dry_run)
