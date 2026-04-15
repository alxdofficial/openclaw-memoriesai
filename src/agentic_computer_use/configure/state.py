"""Read the *effective* DETM config from its three sources.

Precedence (highest to lowest):
  1. Current process env (useful for testing)
  2. Systemd unit `Environment=` lines  (what the daemon actually gets)
  3. Defaults from `agentic_computer_use.config`

For OpenClaw-owned knobs (MCP registration, browser path) we only read
from ~/.openclaw/openclaw.json — those aren't env-driven.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config as cfg

OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"
SYSTEMD_UNIT = Path("/etc/systemd/system/detm-daemon.service")

# Env vars we allow configure to manage, grouped by section.
# Values here serve as the "schema" — prompts + --set keys come from this.
SECTION_ENV: dict[str, list[tuple[str, str]]] = {
    "vision": [
        ("ACU_VISION_BACKEND",          "openrouter|ollama|vllm|claude|passthrough"),
        ("ACU_OPENROUTER_VISION_MODEL", "OpenRouter model id (vision backend only)"),
        ("ACU_VISION_MODEL",            "Ollama model name (vision backend only)"),
        ("ACU_VLLM_URL",                "vLLM base url"),
        ("ACU_VLLM_MODEL",              "vLLM model"),
        ("ACU_CLAUDE_VISION_MODEL",     "Claude vision model"),
    ],
    "gui-agent": [
        ("ACU_GUI_AGENT_BACKEND",   "direct|uitars|claude_cu|omniparser"),
        ("ACU_OPENROUTER_LIVE_MODEL", "OpenRouter model for live_ui / gui_agent"),
        ("ACU_UITARS_OPENROUTER_MODEL", "UI-TARS grounding model (OpenRouter)"),
        ("ACU_UITARS_OLLAMA_MODEL",     "UI-TARS grounding model (local Ollama)"),
        ("ACU_UITARS_KEEP_ALIVE",       "Ollama keep_alive window"),
    ],
    "keys": [
        ("OPENROUTER_API_KEY", "OpenRouter API key"),
        ("ANTHROPIC_API_KEY",  "Anthropic API key"),
        ("MAVI_API_KEY",       "Memories.AI API key (optional)"),
    ],
    "runtime": [
        ("ACU_DEBUG",                "1 to enable verbose logging"),
        ("ACU_STUCK_DETECTION",      "1 to enable stuck-task alerts"),
        ("ACU_DESKTOP_LOOK_DIM",     "Max dim for desktop_look JPEG"),
        ("ACU_DESKTOP_LOOK_QUALITY", "JPEG quality 1-100"),
        ("ACU_MAX_RECORDINGS_MB",    "Cap on recordings dir size"),
    ],
    "workspace": [
        ("ACU_WORKSPACE", "Workspace dir for memory/skills (default ~/.openclaw/workspace)"),
        ("ACU_DATA_DIR",  "DETM data dir (default ~/.agentic-computer-use)"),
    ],
    "display": [
        ("DISPLAY",                     "X display (default :99)"),
        ("ACU_TASK_DISPLAY_WIDTH",      "Default task display width"),
        ("ACU_TASK_DISPLAY_HEIGHT",     "Default task display height"),
    ],
}


@dataclass
class EffectiveConfig:
    process_env: dict[str, str] = field(default_factory=dict)
    unit_env: dict[str, str]    = field(default_factory=dict)
    openclaw: dict[str, Any]    = field(default_factory=dict)

    def get(self, key: str, default: str = "") -> tuple[str, str]:
        """Return (value, source). source ∈ {env, unit, default, unset}."""
        if key in self.process_env and self.process_env[key]:
            return self.process_env[key], "env"
        if key in self.unit_env and self.unit_env[key]:
            return self.unit_env[key], "unit"
        fallback = _config_default_for(key)
        if fallback is not None:
            return fallback, "default"
        return default, "unset"

    def mcp_entry(self) -> dict[str, Any]:
        return (
            self.openclaw.get("mcp", {})
            .get("servers", {})
            .get("agentic-computer-use", {})
        )

    def browser_path(self) -> str:
        return self.openclaw.get("browser", {}).get("executablePath", "")


# Map env var name → config.py attribute name (so --show reveals the compiled-in default)
_CFG_ATTR = {
    "ACU_VISION_BACKEND":          "VISION_BACKEND",
    "ACU_OPENROUTER_VISION_MODEL": "OPENROUTER_VISION_MODEL",
    "ACU_VISION_MODEL":            "VISION_MODEL",
    "ACU_VLLM_URL":                "VLLM_URL",
    "ACU_VLLM_MODEL":              "VLLM_MODEL",
    "ACU_CLAUDE_VISION_MODEL":     "CLAUDE_VISION_MODEL",
    "ACU_GUI_AGENT_BACKEND":       "GUI_AGENT_BACKEND",
    "ACU_OPENROUTER_LIVE_MODEL":   "OPENROUTER_LIVE_MODEL",
    "ACU_UITARS_OPENROUTER_MODEL": "UITARS_OPENROUTER_MODEL",
    "ACU_UITARS_OLLAMA_MODEL":     "UITARS_OLLAMA_MODEL",
    "ACU_UITARS_KEEP_ALIVE":       "UITARS_KEEP_ALIVE",
    "ACU_DESKTOP_LOOK_DIM":        "DESKTOP_LOOK_MAX_DIM",
    "ACU_DESKTOP_LOOK_QUALITY":    "DESKTOP_LOOK_JPEG_QUALITY",
    "ACU_MAX_RECORDINGS_MB":       "MAX_RECORDINGS_MB",
    "ACU_TASK_DISPLAY_WIDTH":      "DEFAULT_TASK_DISPLAY_WIDTH",
    "ACU_TASK_DISPLAY_HEIGHT":     "DEFAULT_TASK_DISPLAY_HEIGHT",
    "ACU_DATA_DIR":                "DATA_DIR",
    "DISPLAY":                     "DISPLAY",
    "OPENROUTER_API_KEY":          "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY":           "CLAUDE_API_KEY",
    "MAVI_API_KEY":                "MAVI_API_KEY",
}


def _config_default_for(env_key: str) -> str | None:
    attr = _CFG_ATTR.get(env_key)
    if not attr:
        return None
    val = getattr(cfg, attr, None)
    return None if val is None else str(val)


def read_unit_env(unit_path: Path = SYSTEMD_UNIT) -> dict[str, str]:
    env: dict[str, str] = {}
    if not unit_path.exists():
        return env
    try:
        for line in unit_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("Environment="):
                kv = line[len("Environment="):]
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    env[k] = v
    except (OSError, UnicodeDecodeError):
        pass
    return env


def read_openclaw_config(path: Path = OPENCLAW_CONFIG) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def load_effective() -> EffectiveConfig:
    return EffectiveConfig(
        process_env=dict(os.environ),
        unit_env=read_unit_env(),
        openclaw=read_openclaw_config(),
    )


def section_keys(section: str) -> list[tuple[str, str]]:
    """Return list of (env_key, description) for a section, or [] if unknown."""
    return SECTION_ENV.get(section, [])


def all_section_names() -> list[str]:
    return [
        "vision", "gui-agent", "keys", "display", "services",
        "workspace", "runtime", "mcp", "dashboard",
    ]
