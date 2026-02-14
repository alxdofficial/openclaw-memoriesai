"""Per-job rolling context window for temporal reasoning."""
import time
from collections import deque
from dataclasses import dataclass, field
from .. import config


@dataclass
class FrameSnapshot:
    jpeg: bytes         # full resolution
    thumbnail: bytes    # 360p thumbnail
    timestamp: float


@dataclass
class Verdict:
    result: str         # "watching" | "partial" | "resolved"
    description: str
    timestamp: float


class JobContext:
    """Rolling context window — previous frames + verdicts + timing."""

    def __init__(self):
        self.frames: deque[FrameSnapshot] = deque(maxlen=config.MAX_CONTEXT_FRAMES)
        self.verdicts: deque[Verdict] = deque(maxlen=config.MAX_CONTEXT_VERDICTS)
        self.started_at: float = time.time()
        self.last_change_at: float = time.time()

    def add_frame(self, jpeg: bytes, thumbnail: bytes, timestamp: float):
        self.frames.append(FrameSnapshot(jpeg=jpeg, thumbnail=thumbnail, timestamp=timestamp))
        self.last_change_at = timestamp

    def add_verdict(self, result: str, description: str, timestamp: float):
        self.verdicts.append(Verdict(result=result, description=description, timestamp=timestamp))

    def build_prompt(self, criteria: str) -> tuple[str, list[bytes]]:
        """Build evaluation prompt with full temporal context."""
        elapsed = time.time() - self.started_at
        since_change = time.time() - self.last_change_at

        verdict_lines = "\n".join(
            f"- [{_format_ago(v.timestamp)}] {v.result.upper()}: {v.description}"
            for v in self.verdicts
        ) or "None yet (first evaluation)."

        text = f"""Look at the screenshot carefully. Read all visible text.

CONDITION TO CHECK: {criteria}

Is this condition met in the screenshot? Read the text on screen word by word before answering.
Respond with exactly one line starting with YES, NO, or PARTIAL:
- YES: <what you see that confirms it>
- NO: <what you see instead>
- PARTIAL: <signs of progress>"""

        # Thumbnails for history + full res for current
        images: list[bytes] = []
        frame_list = list(self.frames)
        for f in frame_list[:-1]:
            images.append(f.thumbnail)
        if frame_list:
            images.append(frame_list[-1].jpeg)  # current = full res

        return text, images


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}min"
    return f"{seconds/3600:.1f}h"


def _format_ago(timestamp: float) -> str:
    return _format_duration(time.time() - timestamp) + " ago"
