"""xdotool-based GUI action execution — shared across live providers."""
import logging
import os
import re
import subprocess

log = logging.getLogger(__name__)

_MOUSE_BUTTON = {"left": "1", "middle": "2", "right": "3"}
_SCROLL_BUTTON = {"up": "4", "down": "5", "left": "6", "right": "7"}


def _xdotool(args: list, display: str, timeout: int = 5) -> str:
    """Run xdotool with the given display env. Returns "ok" or an error string."""
    env = {**os.environ, "DISPLAY": display}
    try:
        result = subprocess.run(
            ["xdotool"] + args,
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        if result.returncode != 0:
            log.warning(f"xdotool {args[0]} failed: {result.stderr.strip()[:200]}")
            return f"error: {result.stderr.strip()[:200]}"
        return "ok"
    except FileNotFoundError:
        return "error: xdotool not found"
    except subprocess.TimeoutExpired:
        return "error: timeout"


def _normalize_key(key: str) -> str:
    """Normalize modifier key case for xdotool: Ctrl+c → ctrl+c."""
    modifiers = {"ctrl", "alt", "shift", "super", "meta"}
    parts = key.split("+")
    return "+".join(p.lower() if p.lower() in modifiers else p for p in parts)


def execute_action(name: str, args: dict, display: str) -> str:
    """Execute a named GUI action via xdotool. Returns 'ok' or an error string."""
    if name == "click":
        x, y = int(args["x"]), int(args["y"])
        btn = _MOUSE_BUTTON.get(str(args.get("button", "left")), "1")
        return _xdotool(["mousemove", "--sync", str(x), str(y), "click", btn], display)

    if name == "double_click":
        x, y = int(args["x"]), int(args["y"])
        return _xdotool(
            ["mousemove", "--sync", str(x), str(y), "click", "--repeat", "2", "1"],
            display,
        )

    if name == "type_text":
        text = args.get("text", "")
        return _xdotool(["type", "--clearmodifiers", "--", text], display)

    if name == "key_press":
        key = _normalize_key(args.get("key", "Return"))
        return _xdotool(["key", "--clearmodifiers", key], display)

    if name == "scroll":
        x, y = int(args["x"]), int(args["y"])
        direction = args.get("direction", "down")
        amount = max(1, int(args.get("amount", 3)))
        btn = _SCROLL_BUTTON.get(direction, "5")
        _xdotool(["mousemove", "--sync", str(x), str(y)], display)
        return _xdotool(["click", "--repeat", str(amount), btn], display)

    log.warning(f"Unknown live action: {name}")
    return f"error: unknown action {name}"
