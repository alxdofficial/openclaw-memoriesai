"""Configuration for agentic-computer-use (DETM)."""
from pathlib import Path
import os

# Paths
DATA_DIR = Path(os.environ.get("ACU_DATA_DIR", Path.home() / ".agentic-computer-use"))
DB_PATH = DATA_DIR / "data.db"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"

# Display
DISPLAY = os.environ.get("DISPLAY", ":99")
DEFAULT_TASK_DISPLAY_WIDTH = int(os.environ.get("ACU_TASK_DISPLAY_WIDTH", "1280"))
DEFAULT_TASK_DISPLAY_HEIGHT = int(os.environ.get("ACU_TASK_DISPLAY_HEIGHT", "720"))

# Vision backend selection
VISION_BACKEND = os.environ.get("ACU_VISION_BACKEND", "ollama")  # ollama|vllm|claude|passthrough

# Ollama
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
VISION_MODEL = os.environ.get("ACU_VISION_MODEL", "minicpm-v")
VISION_SYSTEM_INSTRUCTIONS = os.environ.get(
    "ACU_VISION_SYSTEM_INSTRUCTIONS",
    (
        "You are SmartWait, a visual condition evaluator for GUI/terminal screenshots. "
        "Use visible evidence in the provided images to decide if a wait condition is satisfied. "
        "Be decisive: if the evidence is reasonably clear, resolve — do not keep watching just to be safe. "
        "Only keep watching if the evidence is genuinely absent or impossible to read. "
        "You must follow the output contract in the user prompt exactly."
    ),
)

# vLLM (for UI-TARS, Qwen, etc.)
VLLM_URL = os.environ.get("ACU_VLLM_URL", "http://localhost:8000")
VLLM_MODEL = os.environ.get("ACU_VLLM_MODEL", "ui-tars-1.5-7b")

# Claude vision
CLAUDE_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_VISION_MODEL = os.environ.get("ACU_CLAUDE_VISION_MODEL", "claude-sonnet-4-20250514")

# OpenClaw CLI path
OPENCLAW_CLI = os.environ.get("ACU_OPENCLAW_CLI", "openclaw")

# GUI Agent
GUI_AGENT_BACKEND = os.environ.get("ACU_GUI_AGENT_BACKEND", "direct")  # uitars|claude_cu|direct

# Smart Wait confidence / partial-streak thresholds
RESOLVE_CONFIDENCE_THRESHOLD = float(os.environ.get("ACU_RESOLVE_CONFIDENCE", "0.75"))
PARTIAL_STREAK_RESOLVE = int(os.environ.get("ACU_PARTIAL_STREAK_RESOLVE", "3"))

# OpenClaw
OPENCLAW_GATEWAY_PORT = int(os.environ.get("OPENCLAW_GATEWAY_PORT", "18789"))

# Smart Wait defaults
DEFAULT_POLL_INTERVAL = 2.0  # seconds
MIN_POLL_INTERVAL = 0.5
MAX_POLL_INTERVAL = 15.0
DEFAULT_TIMEOUT = 300  # seconds
PIXEL_DIFF_THRESHOLD = 0.01  # 1% of pixels must change
MAX_STATIC_SECONDS = 30  # force vision re-eval even if diff gate says STATIC
FRAME_MAX_DIM = 1920  # send full resolution — small models miss text at lower res
FRAME_JPEG_QUALITY = 80
THUMBNAIL_MAX_DIM = 360
THUMBNAIL_JPEG_QUALITY = 60
MAX_CONTEXT_FRAMES = 4
MAX_CONTEXT_VERDICTS = 3

def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
