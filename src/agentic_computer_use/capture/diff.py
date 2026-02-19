"""Pixel-diff gate — skip model evaluation when screen hasn't changed."""
import numpy as np
from .. import config


class PixelDiffGate:
    """Compares consecutive frames; returns True if enough pixels changed."""

    def __init__(self, threshold: float = None):
        self.threshold = threshold or config.PIXEL_DIFF_THRESHOLD
        self.last_frame: np.ndarray | None = None
        self.last_diff_pct: float = 0.0

    def should_evaluate(self, frame: np.ndarray) -> bool:
        if self.last_frame is None:
            self.last_frame = frame.copy()
            self.last_diff_pct = 1.0
            return True  # always evaluate first frame

        # Handle window resizes/geometry changes: reset history and force evaluation.
        # X11 windows can change resolution (e.g., toolbar open/close), which can
        # change frame shape and break direct subtraction.
        if frame.shape != self.last_frame.shape:
            self.last_frame = frame.copy()
            self.last_diff_pct = 1.0
            return True

        # Fast pixel comparison
        diff = np.abs(frame.astype(np.int16) - self.last_frame.astype(np.int16))
        self.last_diff_pct = float(np.mean(diff > 10))  # pixels with >10 intensity change

        self.last_frame = frame.copy()
        return self.last_diff_pct > self.threshold

    def reset(self):
        self.last_frame = None
        self.last_diff_pct = 0.0
