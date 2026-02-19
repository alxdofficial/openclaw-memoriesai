"""Screen recording via ffmpeg for video comprehension."""
import signal
import subprocess
import os
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
from .. import config

log = logging.getLogger(__name__)

RECORDINGS_DIR = config.DATA_DIR / "recordings"


def ensure_recordings_dir():
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class AsyncRecording:
    """Handle for a running ffmpeg recording (Popen-based)."""
    process: subprocess.Popen
    output_path: str
    task_id: str
    started_at: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> float:
        return time.time() - self.started_at

    @property
    def is_running(self) -> bool:
        return self.process.poll() is None

    def stop(self) -> dict:
        """Send SIGINT for graceful ffmpeg stop, return recording info."""
        if self.is_running:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

        duration = self.elapsed
        path = Path(self.output_path)
        size_mb = path.stat().st_size / 1024 / 1024 if path.exists() else 0
        log.info(f"Stopped recording: {path.name} ({duration:.1f}s, {size_mb:.1f}MB)")
        return {"path": self.output_path, "duration": round(duration, 1), "size_mb": round(size_mb, 2)}


def _get_root_geometry(display: str) -> str:
    """Query the root window geometry for *display* to get the real resolution."""
    try:
        from ..display.manager import get_xlib_display
        xdisplay = get_xlib_display(display)
        root = xdisplay.screen().root
        geom = root.get_geometry()
        return f"{geom.width}x{geom.height}"
    except Exception:
        return "1920x1080"


def _build_capture_args(window_id: int | None, fps: int, display: str = None) -> tuple[str, str]:
    """Resolve video_size and grab_offset for ffmpeg x11grab."""
    display = display or config.DISPLAY
    env = {**os.environ, "DISPLAY": display}
    if window_id:
        try:
            result = subprocess.run(
                ["xdotool", "getwindowgeometry", "--shell", str(window_id)],
                capture_output=True, text=True, timeout=5, env=env,
            )
            geom = {}
            for line in result.stdout.split("\n"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    geom[k.strip()] = v.strip()
            x, y = int(geom.get("X", 0)), int(geom.get("Y", 0))
            w, h = int(geom.get("WIDTH", 1920)), int(geom.get("HEIGHT", 1080))
            return f"{w}x{h}", f"+{x},{y}"
        except Exception:
            pass
    return _get_root_geometry(display), "+0,0"


def start_recording(task_id: str, window_id: int | None = None, fps: int = 5, display: str = None) -> AsyncRecording:
    """Start an async ffmpeg recording (runs until stopped)."""
    display = display or config.DISPLAY
    ensure_recordings_dir()
    output_name = f"rec_{task_id}_{int(time.time())}.mp4"
    output_path = str(RECORDINGS_DIR / output_name)
    video_size, grab_offset = _build_capture_args(window_id, fps, display=display)
    env = {**os.environ, "DISPLAY": display}

    cmd = [
        "ffmpeg", "-y",
        "-f", "x11grab",
        "-video_size", video_size,
        "-framerate", str(fps),
        "-i", f"{display}{grab_offset}",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    log.info(f"Started async recording for task {task_id}: {output_name}")
    return AsyncRecording(process=proc, output_path=output_path, task_id=task_id)


def record_screen(
    duration: int,
    output_name: str = None,
    fps: int = 5,
    window_id: int = None,
    display: str = None,
) -> str | None:
    """Record the screen/window to an MP4 file.

    Returns the output file path, or None on failure.
    """
    display = display or config.DISPLAY
    ensure_recordings_dir()
    output_name = output_name or f"rec_{int(time.time())}.mp4"
    output_path = str(RECORDINGS_DIR / output_name)

    env = {**os.environ, "DISPLAY": display}
    video_size, grab_offset = _build_capture_args(window_id, fps, display=display)

    cmd = [
        "ffmpeg", "-y",
        "-f", "x11grab",
        "-video_size", video_size,
        "-framerate", str(fps),
        "-i", f"{display}{grab_offset}",
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        output_path
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=duration + 10, env=env
        )
        if result.returncode == 0 and Path(output_path).exists():
            size_mb = Path(output_path).stat().st_size / 1024 / 1024
            log.info(f"Recorded {duration}s to {output_path} ({size_mb:.1f}MB)")
            return output_path
        else:
            log.error(f"ffmpeg failed: {result.stderr[:500]}")
            return None
    except subprocess.TimeoutExpired:
        log.error("Recording timed out")
        return None
    except FileNotFoundError:
        log.error("ffmpeg not found")
        return None


def cleanup_old_recordings(max_age_hours: int = 24):
    """Delete recordings older than max_age_hours."""
    ensure_recordings_dir()
    cutoff = time.time() - (max_age_hours * 3600)
    for f in RECORDINGS_DIR.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink()
            log.info(f"Cleaned up old recording: {f.name}")
