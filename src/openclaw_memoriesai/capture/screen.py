"""Screen capture via X11 (works with Xvfb)."""
import subprocess
import os
import io
import numpy as np
from PIL import Image
from .. import config

# Cache the display connection
_display = None

def _get_display():
    global _display
    if _display is None:
        import Xlib.display
        try:
            _display = Xlib.display.Display(config.DISPLAY)
        except Exception:
            # Reset and retry
            _display = None
            raise
    return _display


def find_window_by_name(name: str) -> int | None:
    """Find an X11 window ID by title substring using xdotool."""
    try:
        result = subprocess.run(
            ["xdotool", "search", "--name", name],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "DISPLAY": config.DISPLAY}
        )
        if result.stdout.strip():
            return int(result.stdout.strip().split("\n")[0])
    except (subprocess.TimeoutExpired, ValueError):
        pass
    return None


def _x11_capture(window) -> np.ndarray | None:
    """Capture an X11 window/root as numpy RGB array."""
    import Xlib.X
    try:
        geom = window.get_geometry()
        raw = window.get_image(0, 0, geom.width, geom.height, Xlib.X.ZPixmap, 0xffffffff)
        # Raw is BGRA (32-bit) or BGR (24-bit) depending on depth
        data = raw.data
        if geom.depth == 24 or geom.depth == 32:
            arr = np.frombuffer(data, dtype=np.uint8).reshape(geom.height, geom.width, 4)
            # BGRA → RGB
            return arr[:, :, [2, 1, 0]].copy()
        return None
    except Exception:
        return None


def capture_window(window_id: int) -> np.ndarray | None:
    """Capture a window as numpy array via X11."""
    try:
        display = _get_display()
        window = display.create_resource_object('window', window_id)
        return _x11_capture(window)
    except Exception:
        return None


def capture_screen() -> np.ndarray | None:
    """Capture the full virtual desktop via X11."""
    try:
        display = _get_display()
        root = display.screen().root
        return _x11_capture(root)
    except Exception:
        return None


def frame_to_jpeg(frame: np.ndarray, max_dim: int = None, quality: int = None) -> bytes:
    """Resize and JPEG-compress a frame for model input."""
    max_dim = max_dim or config.FRAME_MAX_DIM
    quality = quality or config.FRAME_JPEG_QUALITY
    img = Image.fromarray(frame)
    ratio = max_dim / max(img.size)
    if ratio < 1:
        img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def frame_to_thumbnail(frame: np.ndarray) -> bytes:
    """Create a small thumbnail for context history."""
    return frame_to_jpeg(frame, config.THUMBNAIL_MAX_DIM, config.THUMBNAIL_JPEG_QUALITY)
