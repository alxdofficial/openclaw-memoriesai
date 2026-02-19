"""Save action screenshots to disk and return file references."""
import logging
import numpy as np

from . import config
from .capture.screen import frame_to_jpeg, frame_to_thumbnail

log = logging.getLogger(__name__)


def save_screenshot(action_id: str, role: str, frame: np.ndarray) -> dict:
    """Save full + thumbnail JPEGs from a numpy frame. Returns {"full": ..., "thumb": ...}."""
    full_bytes = frame_to_jpeg(frame)
    thumb_bytes = frame_to_thumbnail(frame)
    return save_screenshot_from_jpeg(action_id, role, full_bytes, thumb_bytes)


def save_screenshot_from_jpeg(action_id: str, role: str, jpeg_bytes: bytes, thumb_bytes: bytes) -> dict:
    """Save pre-encoded full + thumbnail JPEGs to disk. Returns {"full": ..., "thumb": ...}."""
    full_name = f"{action_id}_{role}.jpg"
    thumb_name = f"{action_id}_{role}_thumb.jpg"

    full_path = config.SCREENSHOTS_DIR / full_name
    thumb_path = config.SCREENSHOTS_DIR / thumb_name

    try:
        full_path.write_bytes(jpeg_bytes)
        thumb_path.write_bytes(thumb_bytes)
    except Exception as e:
        log.error(f"Failed to save screenshot {full_name}: {e}")
        return {}

    return {"full": full_name, "thumb": thumb_name}
