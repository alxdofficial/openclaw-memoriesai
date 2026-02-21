"""Per-job context — timing and YES/NO prompt generation for SmartWait."""
import time


class JobContext:
    """Tracks start time for elapsed reporting in prompts."""

    def __init__(self):
        self.started_at: float = time.time()


def build_prompt(criteria: str, elapsed: float) -> str:
    """Build a minimal YES/NO evaluation prompt for the vision model."""
    return (
        f"Look at this screenshot and tell me if the following condition is met.\n\n"
        f"CONDITION: {criteria}\n\n"
        f"Time elapsed waiting: {_format_duration(elapsed)}\n\n"
        "Reply with ONLY one of these two formats:\n"
        "YES: <one sentence of visible evidence confirming the condition is met>\n"
        "NO: <one sentence explaining what is missing or not yet visible>"
    )


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds / 60:.1f}min"
    return f"{seconds / 3600:.1f}h"
