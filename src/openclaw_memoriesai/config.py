"""Configuration for openclaw-memoriesai."""
from pathlib import Path
import os

# Paths
DATA_DIR = Path(os.environ.get("MEMORIESAI_DATA_DIR", Path.home() / ".openclaw-memoriesai"))
DB_PATH = DATA_DIR / "data.db"

# Display
DISPLAY = os.environ.get("DISPLAY", ":99")

# Ollama
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
VISION_MODEL = os.environ.get("MEMORIESAI_VISION_MODEL", "minicpm-v")

# OpenClaw
OPENCLAW_GATEWAY_PORT = int(os.environ.get("OPENCLAW_GATEWAY_PORT", "18789"))

# Smart Wait defaults
DEFAULT_POLL_INTERVAL = 2.0  # seconds
MIN_POLL_INTERVAL = 0.5
MAX_POLL_INTERVAL = 15.0
DEFAULT_TIMEOUT = 300  # seconds
PIXEL_DIFF_THRESHOLD = 0.01  # 1% of pixels must change
FRAME_MAX_DIM = 1920  # send full resolution — small models miss text at lower res
FRAME_JPEG_QUALITY = 80
THUMBNAIL_MAX_DIM = 360
THUMBNAIL_JPEG_QUALITY = 60
MAX_CONTEXT_FRAMES = 4
MAX_CONTEXT_VERDICTS = 3

def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
