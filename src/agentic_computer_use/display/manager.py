"""Per-task virtual display manager — Xvfb lifecycle and Xlib connection cache."""
import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field

from .. import config

log = logging.getLogger(__name__)

# ─── Data ────────────────────────────────────────────────────────


@dataclass
class DisplayInfo:
    task_id: str
    display_num: int
    display_str: str
    width: int
    height: int
    process: subprocess.Popen
    wm_process: subprocess.Popen | None = None
    created_at: float = field(default_factory=time.time)


# ─── State ───────────────────────────────────────────────────────

_displays: dict[str, DisplayInfo] = {}  # task_id -> DisplayInfo
_xlib_cache: dict[str, object] = {}     # display_str -> Xlib.display.Display


# ─── Public API ──────────────────────────────────────────────────


def allocate_display(
    task_id: str,
    width: int | None = None,
    height: int | None = None,
) -> DisplayInfo:
    """Start an Xvfb instance for *task_id* and return its DisplayInfo."""
    if task_id in _displays:
        return _displays[task_id]

    width = width or config.DEFAULT_TASK_DISPLAY_WIDTH
    height = height or config.DEFAULT_TASK_DISPLAY_HEIGHT
    num = _find_free_display_num()
    display_str = f":{num}"

    proc = subprocess.Popen(
        [
            "Xvfb", display_str,
            "-screen", "0", f"{width}x{height}x24",
            "-nolisten", "tcp",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Give Xvfb a moment to start, then verify it's alive
    time.sleep(0.3)
    if proc.poll() is not None:
        raise RuntimeError(f"Xvfb failed to start on {display_str} (exit {proc.returncode})")

    # Start fluxbox window manager so the display has a visible desktop immediately
    wm_proc = None
    if _which("fluxbox"):
        try:
            wm_env = {**os.environ, "DISPLAY": display_str}
            wm_proc = subprocess.Popen(
                ["fluxbox", "-no-slit", "-no-toolbar"],
                env=wm_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.2)  # let it draw the desktop background
        except Exception as e:
            log.warning(f"fluxbox failed to start on {display_str}: {e}")

    info = DisplayInfo(
        task_id=task_id,
        display_num=num,
        display_str=display_str,
        width=width,
        height=height,
        process=proc,
        wm_process=wm_proc,
    )
    _displays[task_id] = info
    log.info(f"Allocated display {display_str} ({width}x{height}) for task {task_id}")
    return info


def release_display(task_id: str) -> None:
    """Terminate the Xvfb for *task_id* and clean up."""
    info = _displays.pop(task_id, None)
    if info is None:
        return

    release_xlib_display(info.display_str)

    for proc in filter(None, [info.wm_process, info.process]):
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        except Exception:
            pass

    log.info(f"Released display {info.display_str} for task {task_id}")


def get_display_str(task_id: str) -> str:
    """Return the display string for a task, falling back to the system display."""
    info = _displays.get(task_id)
    return info.display_str if info else config.DISPLAY


def get_xlib_display(display_str: str | None = None):
    """Return a cached Xlib connection for *display_str*."""
    import Xlib.display

    display_str = display_str or config.DISPLAY
    cached = _xlib_cache.get(display_str)
    if cached is not None:
        return cached

    conn = Xlib.display.Display(display_str)
    _xlib_cache[display_str] = conn
    return conn


def release_xlib_display(display_str: str) -> None:
    """Close and remove the cached Xlib connection for *display_str*."""
    conn = _xlib_cache.pop(display_str, None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def cleanup_all() -> None:
    """Tear down every managed display (called on daemon shutdown)."""
    for task_id in list(_displays):
        release_display(task_id)
    for display_str in list(_xlib_cache):
        release_xlib_display(display_str)
    log.info("All task displays cleaned up")


# ─── Internal ────────────────────────────────────────────────────


def _which(cmd: str) -> bool:
    """Return True if cmd is on PATH."""
    import shutil
    return shutil.which(cmd) is not None


def _find_free_display_num(start: int = 100) -> int:
    """Scan from *start* for a display number that is not in use."""
    used = {info.display_num for info in _displays.values()}
    num = start
    while num < 1000:
        if num not in used and not os.path.exists(f"/tmp/.X{num}-lock"):
            return num
        num += 1
    raise RuntimeError("No free display numbers available")
