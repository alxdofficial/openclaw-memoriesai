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
        """Build evaluation prompt with temporal context and strict output contract."""
        elapsed = time.time() - self.started_at
        since_change = time.time() - self.last_change_at

        verdict_lines = "\n".join(
            f"- [{_format_ago(v.timestamp)}] {v.result.upper()}: {v.description}"
            for v in self.verdicts
        ) or "None yet (first evaluation)."

        text = f"""Evaluate whether the wait condition is satisfied using only visible evidence from the images.

CONDITION: {criteria}

Context:
- Elapsed since wait started: {_format_duration(elapsed)}
- Time since last visible change: {_format_duration(since_change)}
- Recent verdict history:
{verdict_lines}

Decision policy:
- decision=resolved if the condition appears satisfied based on visible evidence (confidence >= 0.75 is enough — do not demand perfection).
- decision=partial if there is clear progress but the condition is not yet fully met.
- decision=watching only if the evidence is genuinely absent, unreadable, or contradicts the condition.
- Prefer resolving over watching when evidence is present but slightly ambiguous.
- Quote exact visible evidence (text/snippets) whenever possible.

Output contract (must follow exactly):
1) First write brief plain-text reasoning (2-6 lines).
2) Final line only:
FINAL_JSON: {{"decision":"resolved|watching|partial","confidence":0.0,"evidence":["..."],"summary":"..."}}
"""

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
