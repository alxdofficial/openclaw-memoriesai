"""Desktop control via xdotool — mouse, keyboard, window management."""
import subprocess
import os
import logging
from .. import config

log = logging.getLogger(__name__)


def _run_xdotool(*args: str, timeout: int = 5) -> tuple[bool, str]:
    """Run an xdotool command, return (success, output)."""
    env = {**os.environ, "DISPLAY": config.DISPLAY}
    try:
        result = subprocess.run(
            ["xdotool", *args],
            capture_output=True, text=True, timeout=timeout, env=env
        )
        return result.returncode == 0, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except FileNotFoundError:
        return False, "xdotool not found"


# ─── Window Management ──────────────────────────────────────────

def list_windows() -> list[dict]:
    """List all visible windows with id, title, geometry."""
    ok, output = _run_xdotool("search", "--onlyvisible", "--name", "")
    if not ok or not output:
        return []

    windows = []
    for wid_str in output.split("\n"):
        wid = wid_str.strip()
        if not wid:
            continue
        info = get_window_info(int(wid))
        if info:
            windows.append(info)
    return windows


def get_window_info(window_id: int) -> dict | None:
    """Get window title and geometry."""
    ok_name, name = _run_xdotool("getwindowname", str(window_id))
    ok_geom, geom = _run_xdotool("getwindowgeometry", "--shell", str(window_id))
    if not ok_name:
        return None

    info = {"id": window_id, "title": name}
    if ok_geom:
        for line in geom.split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                info[k.strip().lower()] = int(v.strip()) if v.strip().isdigit() else v.strip()
    return info


def find_window(name: str) -> int | None:
    """Find first window matching name substring."""
    ok, output = _run_xdotool("search", "--name", name)
    if ok and output:
        return int(output.split("\n")[0].strip())
    return None


def focus_window(window_id: int) -> bool:
    """Focus and raise a window."""
    ok1, _ = _run_xdotool("windowfocus", "--sync", str(window_id))
    ok2, _ = _run_xdotool("windowraise", str(window_id))
    return ok1 or ok2


def move_window(window_id: int, x: int, y: int) -> bool:
    ok, _ = _run_xdotool("windowmove", str(window_id), str(x), str(y))
    return ok


def resize_window(window_id: int, width: int, height: int) -> bool:
    ok, _ = _run_xdotool("windowsize", str(window_id), str(width), str(height))
    return ok


def minimize_window(window_id: int) -> bool:
    ok, _ = _run_xdotool("windowminimize", str(window_id))
    return ok


def close_window(window_id: int) -> bool:
    ok, _ = _run_xdotool("windowclose", str(window_id))
    return ok


# ─── Mouse Control ──────────────────────────────────────────────

def mouse_move(x: int, y: int) -> bool:
    ok, _ = _run_xdotool("mousemove", str(x), str(y))
    return ok


def mouse_click(button: int = 1) -> bool:
    """Click: 1=left, 2=middle, 3=right."""
    ok, _ = _run_xdotool("click", str(button))
    return ok


def mouse_click_at(x: int, y: int, button: int = 1) -> bool:
    ok, _ = _run_xdotool("mousemove", str(x), str(y), "click", str(button))
    return ok


def mouse_double_click(x: int = None, y: int = None) -> bool:
    if x is not None and y is not None:
        ok, _ = _run_xdotool("mousemove", str(x), str(y), "click", "--repeat", "2", "1")
    else:
        ok, _ = _run_xdotool("click", "--repeat", "2", "1")
    return ok


def mouse_drag(x1: int, y1: int, x2: int, y2: int, button: int = 1) -> bool:
    _run_xdotool("mousemove", str(x1), str(y1))
    _run_xdotool("mousedown", str(button))
    _run_xdotool("mousemove", "--sync", str(x2), str(y2))
    ok, _ = _run_xdotool("mouseup", str(button))
    return ok


def get_mouse_position() -> tuple[int, int] | None:
    ok, output = _run_xdotool("getmouselocation", "--shell")
    if ok:
        pos = {}
        for line in output.split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                pos[k.strip()] = int(v.strip())
        return pos.get("X", 0), pos.get("Y", 0)
    return None


# ─── Keyboard Control ───────────────────────────────────────────

def type_text(text: str, delay_ms: int = 12) -> bool:
    """Type text with optional inter-key delay."""
    ok, _ = _run_xdotool("type", "--delay", str(delay_ms), "--clearmodifiers", text)
    return ok


def press_key(key: str) -> bool:
    """Press a key combination, e.g., 'ctrl+c', 'Return', 'alt+F4'."""
    ok, _ = _run_xdotool("key", "--clearmodifiers", key)
    return ok


def key_down(key: str) -> bool:
    ok, _ = _run_xdotool("keydown", key)
    return ok


def key_up(key: str) -> bool:
    ok, _ = _run_xdotool("keyup", key)
    return ok


# ─── Screenshots (for OS control loop) ─────────────────────────

def take_screenshot(output_path: str = "/tmp/desktop_screenshot.png") -> str | None:
    """Take a screenshot of the entire virtual desktop using scrot or import."""
    env = {**os.environ, "DISPLAY": config.DISPLAY}
    # Try scrot first
    try:
        result = subprocess.run(
            ["scrot", output_path],
            capture_output=True, timeout=5, env=env
        )
        if result.returncode == 0:
            return output_path
    except FileNotFoundError:
        pass

    # Fallback: use our X11 capture
    try:
        from ..capture.screen import capture_screen
        frame = capture_screen()
        if frame is not None:
            from PIL import Image
            img = Image.fromarray(frame)
            img.save(output_path)
            return output_path
    except Exception as e:
        log.error(f"Screenshot failed: {e}")

    return None
