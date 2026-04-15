"""Section registry for `detm configure`."""
from . import (
    vision, gui_agent, keys, display, services,
    workspace, runtime, mcp, dashboard,
)

# Canonical order = the order shown by `detm configure` (full wizard)
REGISTRY = {
    "vision":    vision,
    "gui-agent": gui_agent,
    "keys":      keys,
    "display":   display,
    "services":  services,
    "workspace": workspace,
    "runtime":   runtime,
    "mcp":       mcp,
    "dashboard": dashboard,
}

ALL_SECTION_NAMES = list(REGISTRY.keys())
