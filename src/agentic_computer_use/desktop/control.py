"""Desktop control via xdotool — mouse, keyboard, window management."""
import subprocess
import os
import logging
from .. import config

log = logging.getLogger(__name__)


def _run_xdotool(*args: str, timeout: int = 5, display: str = None) -> tuple[bool, str]:
    """Run an xdotool command, return (success, output)."""
    display = display or config.DISPLAY
    env = {**os.environ, "DISPLAY": display}
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

def list_windows(display: str = None) -> list[dict]:
    """List all visible windows with id, title, geometry."""
    ok, output = _run_xdotool("search", "--onlyvisible", "--name", "", display=display)
    if not ok or not output:
        return []

    windows = []
    for wid_str in output.split("\n"):
        wid = wid_str.strip()
        if not wid:
            continue
        info = get_window_info(int(wid), display=display)
        if info:
            windows.append(info)
    return windows


def get_window_info(window_id: int, display: str = None) -> dict | None:
    """Get window title and geometry."""
    ok_name, name = _run_xdotool("getwindowname", str(window_id), display=display)
    ok_geom, geom = _run_xdotool("getwindowgeometry", "--shell", str(window_id), display=display)
    if not ok_name:
        return None

    info = {"id": window_id, "title": name}
    if ok_geom:
        for line in geom.split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                info[k.strip().lower()] = int(v.strip()) if v.strip().isdigit() else v.strip()
    return info


def find_window(name: str, display: str = None) -> int | None:
    """Find first window matching name substring."""
    ok, output = _run_xdotool("search", "--name", name, display=display)
    if ok and output:
        return int(output.split("\n")[0].strip())
    return None


def find_window_at(x: int, y: int, display: str = None) -> int | None:
    """Find the topmost visible window that contains point (x, y)."""
    windows = list_windows(display=display)
    # list_windows returns windows in no guaranteed order; check each
    best = None
    for w in windows:
        wx = w.get("x", 0)
        wy = w.get("y", 0)
        ww = w.get("width", 0)
        wh = w.get("height", 0)
        if wx <= x <= wx + ww and wy <= y <= wy + wh:
            best = w["id"]  # last match = topmost (xdotool search order)
    return best


def focus_window(window_id: int, display: str = None) -> bool:
    """Focus and raise a window."""
    ok1, _ = _run_xdotool("windowfocus", "--sync", str(window_id), display=display)
    ok2, _ = _run_xdotool("windowraise", str(window_id), display=display)
    return ok1 or ok2


def move_window(window_id: int, x: int, y: int, display: str = None) -> bool:
    ok, _ = _run_xdotool("windowmove", str(window_id), str(x), str(y), display=display)
    return ok


def resize_window(window_id: int, width: int, height: int, display: str = None) -> bool:
    ok, _ = _run_xdotool("windowsize", str(window_id), str(width), str(height), display=display)
    return ok


def minimize_window(window_id: int, display: str = None) -> bool:
    ok, _ = _run_xdotool("windowminimize", str(window_id), display=display)
    return ok


def close_window(window_id: int, display: str = None) -> bool:
    ok, _ = _run_xdotool("windowclose", str(window_id), display=display)
    return ok


# ─── Mouse Control ──────────────────────────────────────────────

def mouse_move(x: int, y: int, display: str = None) -> bool:
    ok, _ = _run_xdotool("mousemove", str(x), str(y), display=display)
    return ok


def mouse_click(button: int = 1, display: str = None) -> bool:
    """Click: 1=left, 2=middle, 3=right."""
    ok, _ = _run_xdotool("click", str(button), display=display)
    return ok


def mouse_click_at(x: int, y: int, button: int = 1, display: str = None) -> bool:
    """Move mouse to (x, y) and click. Uses --sync to ensure move completes before click."""
    _run_xdotool("mousemove", "--sync", str(x), str(y), display=display)
    ok, _ = _run_xdotool("click", str(button), display=display)
    return ok


def mouse_double_click(x: int = None, y: int = None, display: str = None) -> bool:
    if x is not None and y is not None:
        _run_xdotool("mousemove", "--sync", str(x), str(y), display=display)
    ok, _ = _run_xdotool("click", "--repeat", "2", "1", display=display)
    return ok


def mouse_drag(x1: int, y1: int, x2: int, y2: int, button: int = 1, display: str = None) -> bool:
    _run_xdotool("mousemove", str(x1), str(y1), display=display)
    _run_xdotool("mousedown", str(button), display=display)
    _run_xdotool("mousemove", "--sync", str(x2), str(y2), display=display)
    ok, _ = _run_xdotool("mouseup", str(button), display=display)
    return ok


def get_mouse_position(display: str = None) -> tuple[int, int] | None:
    ok, output = _run_xdotool("getmouselocation", "--shell", display=display)
    if ok:
        pos = {}
        for line in output.split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                pos[k.strip()] = int(v.strip())
        return pos.get("X", 0), pos.get("Y", 0)
    return None


# ─── Keyboard Control ───────────────────────────────────────────

def type_text(text: str, delay_ms: int = 12, display: str = None) -> bool:
    """Type text with optional inter-key delay."""
    ok, _ = _run_xdotool("type", "--delay", str(delay_ms), "--clearmodifiers", text, display=display)
    return ok


def press_key(key: str, display: str = None) -> bool:
    """Press a key combination, e.g., 'ctrl+c', 'Return', 'alt+F4'."""
    ok, _ = _run_xdotool("key", "--clearmodifiers", key, display=display)
    return ok


def key_down(key: str, display: str = None) -> bool:
    ok, _ = _run_xdotool("keydown", key, display=display)
    return ok


def key_up(key: str, display: str = None) -> bool:
    ok, _ = _run_xdotool("keyup", key, display=display)
    return ok


# ─── Screenshots (for OS control loop) ─────────────────────────

def take_screenshot(output_path: str = "/tmp/desktop_screenshot.png", display: str = None) -> str | None:
    """Take a screenshot of the entire virtual desktop using scrot or import."""
    display = display or config.DISPLAY
    env = {**os.environ, "DISPLAY": display}
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
        frame = capture_screen(display=display)
        if frame is not None:
            from PIL import Image
            img = Image.fromarray(frame)
            img.save(output_path)
            return output_path
    except Exception as e:
        log.error(f"Screenshot failed: {e}")

    return None
