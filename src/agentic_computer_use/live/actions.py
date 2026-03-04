"""xdotool-based GUI action execution — shared across live providers."""
import logging
import os
import subprocess
import time

log = logging.getLogger(__name__)

_MOUSE_BUTTON = {"left": "1", "middle": "2", "right": "3"}
_SCROLL_BUTTON = {"up": "4", "down": "5", "left": "6", "right": "7"}

# Steps and per-step delay for smooth mouse animation.
# 10 steps × 18 ms ≈ 180 ms total — fast enough to feel instant, slow enough
# to be visible as a glide in the live frame stream.
_SMOOTH_STEPS = 10
_SMOOTH_STEP_MS = 0.018


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


def _smooth_mousemove(x: int, y: int, display: str) -> str:
    """Glide the cursor to (x, y) via incremental steps so the motion is visible in frames."""
    env = {**os.environ, "DISPLAY": display}
    # Get current position
    cx, cy = x, y  # fallback: treat as if already there
    try:
        loc = subprocess.run(
            ["xdotool", "getmouselocation"],
            capture_output=True, text=True, timeout=2, env=env,
        )
        parts = loc.stdout.strip().split()
        cx = int(parts[0].split(":")[1])
        cy = int(parts[1].split(":")[1])
    except Exception:
        pass

    steps = _SMOOTH_STEPS
    for i in range(1, steps + 1):
        ix = cx + (x - cx) * i // steps
        iy = cy + (y - cy) * i // steps
        subprocess.run(
            ["xdotool", "mousemove", str(ix), str(iy)],
            capture_output=True, timeout=2, env=env,
        )
        if i < steps:
            time.sleep(_SMOOTH_STEP_MS)

    return "ok"


def _normalize_key(key: str) -> str:
    """Normalize modifier key case for xdotool: Ctrl+c → ctrl+c."""
    modifiers = {"ctrl", "alt", "shift", "super", "meta"}
    parts = key.split("+")
    return "+".join(p.lower() if p.lower() in modifiers else p for p in parts)


def execute_action(name: str, args: dict, display: str) -> str:
    """Execute a named GUI action via xdotool. Returns 'ok' or an error string."""
    if name == "move_mouse":
        x, y = int(args["x"]), int(args["y"])
        return _smooth_mousemove(x, y, display)

    if name == "click":
        x, y = int(args["x"]), int(args["y"])
        btn = _MOUSE_BUTTON.get(str(args.get("button", "left")), "1")
        _smooth_mousemove(x, y, display)
        return _xdotool(["click", btn], display)

    if name == "double_click":
        x, y = int(args["x"]), int(args["y"])
        _smooth_mousemove(x, y, display)
        return _xdotool(["click", "--repeat", "2", "1"], display)

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
        _smooth_mousemove(x, y, display)
        return _xdotool(["click", "--repeat", str(amount), btn], display)

    log.warning(f"Unknown live action: {name}")
    return f"error: unknown action {name}"


def execute_action_logged(name: str, args: dict, display: str) -> str:
    """Execute a GUI action with success/failure logging and timing."""
    t0 = time.monotonic()
    result = execute_action(name, args, display)
    elapsed_ms = (time.monotonic() - t0) * 1000
    if result == "ok":
        coords = f" at ({args.get('x')},{args.get('y')})" if "x" in args else ""
        log.info(f"Action {name}{coords} — ok ({elapsed_ms:.0f}ms)")
    else:
        log.warning(f"Action {name} — {result} ({elapsed_ms:.0f}ms)")
    return result
