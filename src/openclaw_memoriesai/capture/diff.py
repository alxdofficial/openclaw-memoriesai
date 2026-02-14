"""Pixel-diff gate — skip model evaluation when screen hasn't changed."""
import numpy as np
from .. import config


class PixelDiffGate:
    """Compares consecutive frames; returns True if enough pixels changed."""

    def __init__(self, threshold: float = None):
        self.threshold = threshold or config.PIXEL_DIFF_THRESHOLD
        self.last_frame: np.ndarray | None = None

    def should_evaluate(self, frame: np.ndarray) -> bool:
        if self.last_frame is None:
            self.last_frame = frame.copy()
            return True  # always evaluate first frame

        # Fast pixel comparison
        diff = np.abs(frame.astype(np.int16) - self.last_frame.astype(np.int16))
        changed = np.mean(diff > 10)  # pixels with >10 intensity change

        self.last_frame = frame.copy()
        return changed > self.threshold

    def reset(self):
        self.last_frame = None
