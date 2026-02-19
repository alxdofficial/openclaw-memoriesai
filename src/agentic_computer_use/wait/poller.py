"""Adaptive polling — speeds up when close, slows down when static."""
from .. import config


class AdaptivePoller:
    def __init__(self, base_interval: float = None):
        try:
            self.base = float(base_interval) if base_interval is not None else config.DEFAULT_POLL_INTERVAL
        except (TypeError, ValueError):
            self.base = config.DEFAULT_POLL_INTERVAL
        self.current = self.base
        self.static_count = 0

    def on_no_change(self):
        """Screen hasn't changed (pixel-diff gate skipped)."""
        self.static_count += 1
        if self.static_count > 5:
            self.current = min(self.current * 1.5, config.MAX_POLL_INTERVAL)

    def on_change_no_match(self):
        """Screen changed but condition not met."""
        self.static_count = 0
        self.current = self.base

    def on_partial(self):
        """Model said PARTIAL — getting close, speed up."""
        self.static_count = 0
        self.current = max(self.current * 0.5, config.MIN_POLL_INTERVAL)

    def on_match(self):
        pass

    @property
    def interval(self) -> float:
        return self.current
