"""Desktop control via xdotool — mouse, keyboard, window management."""
import subprocess
import os
import time
import logging
from .. import config
from .. import humanize

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
    """Focus and raise a window in a single xdotool call."""
    ok, _ = _run_xdotool(
        "windowfocus", "--sync", str(window_id),
        "windowraise", str(window_id),
        display=display,
    )
    return ok


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

def _human_move_cursor(x: int, y: int, display: str = None) -> bool:
    """Move cursor to (x, y). When humanize is on, walks a Bezier arc with
    variable timing; when off, teleports (legacy behavior).

    Either way the final position is exactly (x, y) — no jitter on the
    terminal waypoint, so callers can trust the landing.
    """
    if not humanize.is_enabled():
        ok, _ = _run_xdotool("mousemove", "--sync", str(x), str(y), display=display)
        return ok

    pos = get_mouse_position(display=display) or (x, y)
    x0, y0 = pos
    bounds = _screen_bounds(display=display)
    waypoints = humanize.bezier_path(x0, y0, x, y, screen_bounds=bounds)
    for wp in waypoints[:-1]:
        _run_xdotool("mousemove", str(wp.x), str(wp.y), display=display, timeout=2)
        if wp.sleep_before_next_s > 0:
            time.sleep(wp.sleep_before_next_s)
    final = waypoints[-1]
    ok, _ = _run_xdotool("mousemove", "--sync", str(final.x), str(final.y), display=display)
    return ok


def _human_click(button: int = 1, display: str = None) -> bool:
    """Single click with a short mousedown-dwell-mouseup when humanize is on."""
    if not humanize.is_enabled():
        ok, _ = _run_xdotool("click", str(button), display=display)
        return ok
    _run_xdotool("mousedown", str(button), display=display)
    time.sleep(humanize.click_dwell_s())
    ok, _ = _run_xdotool("mouseup", str(button), display=display)
    return ok


def _screen_bounds(display: str = None) -> tuple[int, int, int, int] | None:
    """Return (0, 0, w, h) for the display, or None on failure."""
    display = display or config.DISPLAY
    env = {**os.environ, "DISPLAY": display}
    try:
        r = subprocess.run(["xdpyinfo"], capture_output=True, text=True, timeout=2, env=env)
        if r.returncode != 0:
            return None
        for line in r.stdout.splitlines():
            if "dimensions:" in line:
                dim = line.strip().split("dimensions:", 1)[1].strip().split()[0]
                w, h = (int(v) for v in dim.split("x"))
                return (0, 0, w, h)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def mouse_move(x: int, y: int, display: str = None) -> bool:
    return _human_move_cursor(x, y, display=display)


def mouse_click(button: int = 1, display: str = None) -> bool:
    """Click: 1=left, 2=middle, 3=right."""
    return _human_click(button, display=display)


def mouse_click_at(x: int, y: int, button: int = 1, display: str = None) -> bool:
    """Move mouse to (x, y) and click. Humanize: Bezier arc + click dwell."""
    if not humanize.is_enabled():
        ok, _ = _run_xdotool(
            "mousemove", "--sync", str(x), str(y),
            "click", str(button),
            display=display,
        )
        return ok
    jx, jy = humanize.jitter_click_coords(x, y)
    if not _human_move_cursor(jx, jy, display=display):
        return False
    return _human_click(button, display=display)


def mouse_double_click(x: int = None, y: int = None, display: str = None) -> bool:
    if not humanize.is_enabled():
        if x is not None and y is not None:
            ok, _ = _run_xdotool(
                "mousemove", "--sync", str(x), str(y),
                "click", "--repeat", "2", "1",
                display=display,
            )
        else:
            ok, _ = _run_xdotool("click", "--repeat", "2", "1", display=display)
        return ok
    if x is not None and y is not None:
        jx, jy = humanize.jitter_click_coords(x, y)
        if not _human_move_cursor(jx, jy, display=display):
            return False
    _human_click(1, display=display)
    # Most UIs accept 200-500ms between clicks as a double-click; 60-90ms
    # is snappier and still reliably registers while feeling human.
    time.sleep(humanize.click_dwell_s() + 0.02)
    return _human_click(1, display=display)


def mouse_drag(x1: int, y1: int, x2: int, y2: int, button: int = 1, display: str = None) -> bool:
    if not humanize.is_enabled():
        _run_xdotool("mousemove", str(x1), str(y1), "mousedown", str(button), display=display)
        ok, _ = _run_xdotool("mousemove", "--sync", str(x2), str(y2), "mouseup", str(button), display=display)
        return ok
    # Humanize: arc to start, press, arc to end, release.
    if not _human_move_cursor(x1, y1, display=display):
        return False
    _run_xdotool("mousedown", str(button), display=display)
    time.sleep(humanize.click_dwell_s())  # brief grab pause
    if not _human_move_cursor(x2, y2, display=display):
        _run_xdotool("mouseup", str(button), display=display)
        return False
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
    """Type text. When humanize is on, per-char delays follow a lognormal
    distribution with word-boundary pauses; otherwise uses the flat
    `delay_ms` passed by the caller (legacy 12ms default).
    """
    if not humanize.is_enabled():
        ok, _ = _run_xdotool(
            "type", "--delay", str(delay_ms), "--clearmodifiers", text,
            display=display,
        )
        return ok

    # Humanize: one xdotool call per character so we can vary the delay.
    # --delay 0 because we'll sleep between calls from Python.
    for ch, wait_s in zip(text, humanize.typing_delays(text)):
        ok, _ = _run_xdotool(
            "type", "--delay", "0", "--clearmodifiers", ch,
            display=display, timeout=3,
        )
        if not ok:
            return False
        if wait_s > 0:
            time.sleep(wait_s)
    return True


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
