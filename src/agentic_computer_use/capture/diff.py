"""Pixel-diff gate — skip model evaluation when screen hasn't changed."""
import numpy as np
from .. import config


def _downsample(frame: np.ndarray, max_width: int) -> np.ndarray:
    """Fast integer-stride decimation to reduce diff computation cost."""
    h, w = frame.shape[:2]
    if w <= max_width:
        return frame
    step = max(1, w // max_width)
    return frame[::step, ::step]


class PixelDiffGate:
    """Compares consecutive frames; returns True if enough pixels changed."""

    def __init__(self, threshold: float = None):
        self.threshold = threshold or config.PIXEL_DIFF_THRESHOLD
        self.last_frame: np.ndarray | None = None
        self.last_diff_pct: float = 0.0

    def should_evaluate(self, frame: np.ndarray) -> bool:
        # Downsample before comparison — same accuracy, ~30x faster
        small = _downsample(frame, config.DIFF_MAX_WIDTH)

        if self.last_frame is None:
            self.last_frame = small.copy()
            self.last_diff_pct = 1.0
            return True  # always evaluate first frame

        # Handle window resizes/geometry changes: reset history and force evaluation.
        if small.shape != self.last_frame.shape:
            self.last_frame = small.copy()
            self.last_diff_pct = 1.0
            return True

        # Fast pixel comparison on downsampled frame
        diff = np.abs(small.astype(np.int16) - self.last_frame.astype(np.int16))
        self.last_diff_pct = float(np.mean(diff > 10))  # pixels with >10 intensity change

        self.last_frame = small.copy()
        return self.last_diff_pct > self.threshold

    def reset(self):
        self.last_frame = None
        self.last_diff_pct = 0.0
