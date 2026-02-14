"""Rich debug logging with color-coded categories.

Enable: set MEMORIESAI_DEBUG=1 or pass --debug to daemon.
Logs to both stderr (colored) and a rolling log file.

Categories & colors:
  🟦 BLUE    — daemon lifecycle, config, startup
  🟩 GREEN   — vision model input/output (prompts + responses)
  🟨 YELLOW  — wait engine decisions (polling, diff gate, verdicts)
  🟪 PURPLE  — OpenClaw integration (system events, wake calls)
  🟥 RED     — errors and warnings
  🟧 ORANGE  — HTTP requests (MCP proxy ↔ daemon)
  ⬜ WHITE   — user ↔ OpenClaw messages (intercepted)
  🩵 CYAN    — task memory operations
"""
import logging
import os
import sys
import time
import json
from datetime import datetime, timezone
from pathlib import Path
from . import config

# ANSI color codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

COLORS = {
    "DAEMON":   "\033[34m",      # Blue
    "VISION":   "\033[32m",      # Green
    "WAIT":     "\033[33m",      # Yellow
    "OPENCLAW": "\033[35m",      # Purple/Magenta
    "ERROR":    "\033[31m",      # Red
    "HTTP":     "\033[38;5;208m", # Orange (256-color)
    "USER":     "\033[37m",      # White
    "TASK":     "\033[36m",      # Cyan
}

# Emoji prefixes for file logs (no ANSI)
EMOJI = {
    "DAEMON":   "🟦",
    "VISION":   "🟩",
    "WAIT":     "🟨",
    "OPENCLAW": "🟪",
    "ERROR":    "🟥",
    "HTTP":     "🟧",
    "USER":     "⬜",
    "TASK":     "🩵",
}

_debug_enabled = False
_log_file = None
_log_path = None


def is_enabled() -> bool:
    return _debug_enabled


def init(enabled: bool = None, log_dir: Path = None):
    """Initialize debug logging. Call once at daemon startup."""
    global _debug_enabled, _log_file, _log_path

    if enabled is None:
        enabled = os.environ.get("MEMORIESAI_DEBUG", "0") in ("1", "true", "yes")

    _debug_enabled = enabled

    if not enabled:
        return

    log_dir = log_dir or config.DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _log_path = log_dir / "debug.log"

    # Rotate if over 10MB
    if _log_path.exists() and _log_path.stat().st_size > 10 * 1024 * 1024:
        rotated = log_dir / f"debug.{int(time.time())}.log"
        _log_path.rename(rotated)

    _log_file = open(_log_path, "a", buffering=1)  # line-buffered

    log("DAEMON", f"Debug logging enabled. Log file: {_log_path}")
    log("DAEMON", f"Tail with: tail -f {_log_path}")
    log("DAEMON", f"Or colored: memoriesai-debug-tail")


def log(category: str, message: str, data: dict = None):
    """Log a debug message with category color coding."""
    if not _debug_enabled:
        return

    ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    cat = category.upper()

    # Terminal (colored)
    color = COLORS.get(cat, RESET)
    prefix = f"{DIM}{ts}{RESET} {color}{BOLD}[{cat:8s}]{RESET} {color}"
    line = f"{prefix}{message}{RESET}"
    if data:
        data_str = json.dumps(data, indent=2, default=str)
        # Indent data lines
        indented = "\n".join(f"  {color}{l}{RESET}" for l in data_str.split("\n"))
        line += f"\n{indented}"
    print(line, file=sys.stderr, flush=True)

    # File (emoji, no ANSI)
    emoji = EMOJI.get(cat, "  ")
    file_line = f"{ts} {emoji} [{cat:8s}] {message}"
    if data:
        file_line += f"\n{json.dumps(data, indent=2, default=str)}"
    if _log_file:
        _log_file.write(file_line + "\n")


def log_vision_request(prompt: str, num_images: int, image_sizes: list[int] = None, job_id: str = None):
    """Log a vision model request."""
    sizes = [f"{s//1024}KB" for s in (image_sizes or [])]
    tag = f"[{job_id}] " if job_id else ""
    log("VISION", f"{tag}→ Sending to MiniCPM-o ({num_images} image{'s' if num_images != 1 else ''}, {', '.join(sizes) if sizes else 'no images'})")
    # Show full prompt (indent continuation lines)
    for i, line in enumerate(prompt.strip().split("\n")):
        prefix = "  Prompt: " if i == 0 else "          "
        log("VISION", f"{tag}{prefix}{line}")


def log_vision_response(response: str, duration_ms: float, job_id: str = None):
    """Log a vision model response."""
    tag = f"[{job_id}] " if job_id else ""
    # Show full response (indent continuation lines)
    lines = response.strip().split("\n")
    for i, line in enumerate(lines):
        prefix = f"← Response ({duration_ms:.0f}ms): " if i == 0 else "  " + " " * 20
        log("VISION", f"{tag}{prefix}{line}")


def log_wait_event(job_id: str, event: str, detail: str = ""):
    """Log a wait engine event."""
    log("WAIT", f"[{job_id}] {event}" + (f" — {detail}" if detail else ""))


def log_diff_gate(job_id: str, changed: bool, diff_pct: float = None):
    """Log pixel-diff gate decision."""
    if changed:
        log("WAIT", f"[{job_id}] Diff gate: CHANGED ({diff_pct:.2%} pixels differ) → evaluating")
    else:
        log("WAIT", f"[{job_id}] Diff gate: STATIC → skipping vision")


def log_openclaw_event(event_type: str, message: str, success: bool = True):
    """Log OpenClaw integration events."""
    status = "✓" if success else "✗"
    log("OPENCLAW", f"{status} {event_type}: {message[:200]}")


def log_http(method: str, path: str, status: int, duration_ms: float):
    """Log HTTP request to daemon."""
    log("HTTP", f"{method} {path} → {status} ({duration_ms:.0f}ms)")


def log_user_message(direction: str, content: str):
    """Log user ↔ OpenClaw messages."""
    arrow = "→" if direction == "from_user" else "←"
    label = "User → OpenClaw" if direction == "from_user" else "OpenClaw → User"
    log("USER", f"{arrow} {label}: {content[:300]}")


def log_task(task_id: str, action: str, detail: str = ""):
    """Log task memory operations."""
    log("TASK", f"[{task_id}] {action}" + (f" — {detail}" if detail else ""))


def close():
    """Flush and close log file."""
    global _log_file
    if _log_file:
        _log_file.close()
        _log_file = None
