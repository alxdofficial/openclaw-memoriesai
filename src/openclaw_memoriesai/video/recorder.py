"""Screen recording via ffmpeg for video comprehension."""
import subprocess
import os
import time
import logging
from pathlib import Path
from .. import config

log = logging.getLogger(__name__)

RECORDINGS_DIR = config.DATA_DIR / "recordings"


def ensure_recordings_dir():
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)


def record_screen(
    duration: int,
    output_name: str = None,
    fps: int = 5,
    window_id: int = None,
) -> str | None:
    """Record the screen/window to an MP4 file.
    
    Returns the output file path, or None on failure.
    """
    ensure_recordings_dir()
    output_name = output_name or f"rec_{int(time.time())}.mp4"
    output_path = str(RECORDINGS_DIR / output_name)

    env = {**os.environ, "DISPLAY": config.DISPLAY}

    if window_id:
        # Record a specific window
        # Get window geometry first
        try:
            result = subprocess.run(
                ["xdotool", "getwindowgeometry", "--shell", str(window_id)],
                capture_output=True, text=True, timeout=5, env=env
            )
            geom = {}
            for line in result.stdout.split("\n"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    geom[k.strip()] = v.strip()
            x, y = int(geom.get("X", 0)), int(geom.get("Y", 0))
            w, h = int(geom.get("WIDTH", 1920)), int(geom.get("HEIGHT", 1080))
            video_size = f"{w}x{h}"
            grab_offset = f"+{x},{y}"
        except Exception:
            video_size = "1920x1080"
            grab_offset = "+0,0"
    else:
        video_size = "1920x1080"
        grab_offset = "+0,0"

    cmd = [
        "ffmpeg", "-y",
        "-f", "x11grab",
        "-video_size", video_size,
        "-framerate", str(fps),
        "-i", f"{config.DISPLAY}{grab_offset}",
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
