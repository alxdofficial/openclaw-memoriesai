# Smart Wait — Detailed Design

## Overview

Smart Wait is the core innovation of this project. It allows an LLM agent to delegate waiting to a local vision model, freeing the agent's context and compute for other work while a lightweight daemon monitors the screen.

## Why not just poll?

| Approach | Cost per 5-min wait | Latency | Intelligence |
|----------|-------------------|---------|--------------|
| LLM screenshot polling (5s interval) | ~60 Claude vision calls ($$$) | 5s | High |
| Hardcoded `sleep(300)` | 0 | 300s (wastes time) | None |
| exec notifyOnExit | 0 | Instant (CLI only) | N/A for GUI |
| **Smart Wait (ours)** | **0 (local model)** | **1-5s** | **High** |

## The MiniCPM-o Backbone

### Why MiniCPM-o 4.5?

- **9B parameters** — small enough for CPU inference, fast on GPU
- **Gemini 2.5 Flash level vision** — top-tier for its size class
- **OCR capability** — can read text on screen (error messages, progress percentages, URLs)
- **Runs locally** — no API costs, no latency, no privacy concerns
- **Quantized formats** — Q4_K_M at ~5GB RAM, Q2_K at ~3GB RAM
- **llama.cpp + Ollama support** — easy deployment
- **Full-duplex streaming** — can process video frames in real-time

### Deployment

**Recommended: Ollama**
```bash
# Install
curl -fsSL https://ollama.com/install.sh | sh

# Pull model (Q4 quantization, ~5GB)
ollama pull minicpm-v

# Or for the latest 4.5:
ollama pull minicpm-o:latest
```

**Alternative: llama.cpp server**
```bash
# Download GGUF
wget https://huggingface.co/openbmb/MiniCPM-o-4_5-GGUF/resolve/main/MiniCPM-o-4_5-Q4_K_M.gguf

# Run server
./llama-server -m MiniCPM-o-4_5-Q4_K_M.gguf --port 8090 --ctx-size 4096
```

**Alternative: vLLM (GPU, highest throughput)**
```bash
pip install vllm
vllm serve openbmb/MiniCPM-o-4_5 --port 8090
```

### Hardware Requirements

| Quantization | RAM/VRAM | Speed (CPU) | Speed (GPU) | Quality |
|-------------|----------|-------------|-------------|---------|
| Q2_K | ~3 GB | ~2 tok/s | N/A | Acceptable |
| Q4_K_M | ~5 GB | ~5 tok/s | ~30 tok/s | Good |
| Q8_0 | ~9 GB | ~3 tok/s | ~25 tok/s | Near-original |
| FP16 | ~18 GB | Too slow | ~40 tok/s | Original |

For Smart Wait, we need very short outputs ("YES: download completed" or "NO") so even CPU inference at 2 tok/s is fine — each evaluation takes <1 second.

## Frame Capture Pipeline

### Step 1: Raw Frame Grabbing

```python
# X11 — fast shared memory capture
import Xlib.display
import Xlib.X

def capture_window_x11(window_id: int) -> bytes:
    """Capture a window using X11 SHM extension. ~5ms per frame."""
    display = Xlib.display.Display()
    window = display.create_resource_object('window', window_id)
    geom = window.get_geometry()
    raw = window.get_image(0, 0, geom.width, geom.height, Xlib.X.ZPixmap, 0xffffffff)
    return raw.data

# Fallback: ImageMagick import (slower, ~50ms)
import subprocess
def capture_window_import(window_id: int, output_path: str):
    subprocess.run(["import", "-window", hex(window_id), output_path])

# PTY: just read the buffer
def capture_pty(session_id: str) -> str:
    """Read terminal buffer — no vision needed."""
    # Use OpenClaw's process tool or direct PTY read
    ...
```

### Step 2: Pixel-Diff Gate

```python
import numpy as np

class PixelDiffGate:
    def __init__(self, threshold: float = 0.01):
        self.threshold = threshold  # 1% of pixels must change
        self.last_frame: np.ndarray | None = None
    
    def should_evaluate(self, frame: np.ndarray) -> bool:
        """Returns True if the frame has changed enough to warrant evaluation."""
        if self.last_frame is None:
            self.last_frame = frame
            return True
        
        # Fast pixel comparison
        diff = np.abs(frame.astype(int) - self.last_frame.astype(int))
        changed_pixels = np.sum(diff > 10) / diff.size  # >10 intensity change
        
        self.last_frame = frame
        return changed_pixels > self.threshold
```

This gate costs essentially zero CPU — a single numpy comparison. It prevents sending identical frames to the model when nothing is happening on screen.

### Step 3: Resize + Compress

```python
from PIL import Image
import io

def prepare_frame(frame: np.ndarray, max_dim: int = 720) -> bytes:
    """Resize and JPEG compress for model input."""
    img = Image.fromarray(frame)
    
    # Resize maintaining aspect ratio
    ratio = max_dim / max(img.size)
    if ratio < 1:
        img = img.resize((int(img.width * ratio), int(img.height * ratio)))
    
    # JPEG compress
    buffer = io.BytesIO()
    img.save(buffer, format='JPEG', quality=80)
    return buffer.getvalue()  # ~50-100KB
```

### Step 4: Model Evaluation

```python
async def evaluate_condition(
    frame_jpeg: bytes,
    criteria: str,
    model_client: OllamaClient
) -> tuple[str, str]:  # (verdict, description)
    """Ask MiniCPM-o if the condition is met."""
    
    response = await model_client.generate(
        model="minicpm-v",
        prompt=f"""Look at this screenshot and evaluate the following condition:

CONDITION: {criteria}

Respond with exactly one of:
- YES: <brief description of what you see that satisfies the condition>
- NO: <brief description of current state>
- PARTIAL: <brief description of progress toward the condition>

Be concise. One line only.""",
        images=[frame_jpeg]
    )
    
    text = response.strip()
    if text.startswith("YES"):
        return ("resolved", text[5:].strip())
    elif text.startswith("PARTIAL"):
        return ("partial", text[9:].strip())
    else:
        return ("watching", text[4:].strip() if text.startswith("NO") else text)
```

## Adaptive Polling

```python
class AdaptivePoller:
    """Adjusts polling rate based on screen activity and model feedback."""
    
    def __init__(self):
        self.base_interval = 2.0  # seconds
        self.current_interval = 2.0
        self.min_interval = 0.5
        self.max_interval = 15.0
        self.static_count = 0
    
    def on_no_change(self):
        """Screen hasn't changed (pixel-diff gate said skip)."""
        self.static_count += 1
        if self.static_count > 5:
            self.current_interval = min(self.current_interval * 1.5, self.max_interval)
    
    def on_change_no_match(self):
        """Screen changed but condition not met."""
        self.static_count = 0
        self.current_interval = self.base_interval
    
    def on_partial_match(self):
        """Model said PARTIAL — getting close."""
        self.static_count = 0
        self.current_interval = max(self.current_interval * 0.5, self.min_interval)
    
    def on_match(self):
        """Condition met — stop polling."""
        pass
    
    @property
    def interval(self) -> float:
        return self.current_interval
```

## Multiplexing Multiple Wait Jobs

When multiple waits are active simultaneously, we use a priority queue:

```python
import heapq
import time

class WaitScheduler:
    """Schedules frame captures and model evaluations across multiple wait jobs."""
    
    def __init__(self, model_client):
        self.jobs: dict[str, WaitJob] = {}
        self.model = model_client
    
    async def run(self):
        while self.jobs:
            # Find the job that's most overdue for a check
            now = time.time()
            next_job = min(
                self.jobs.values(),
                key=lambda j: j.next_check_at
            )
            
            if next_job.next_check_at > now:
                await asyncio.sleep(next_job.next_check_at - now)
            
            # Capture frame
            frame = capture_frame(next_job.target)
            
            # Pixel-diff gate
            if not next_job.diff_gate.should_evaluate(frame):
                next_job.poller.on_no_change()
                next_job.next_check_at = now + next_job.poller.interval
                continue
            
            # Evaluate
            prepared = prepare_frame(frame)
            verdict, description = await evaluate_condition(
                prepared, next_job.criteria, self.model
            )
            
            if verdict == "resolved":
                await self.resolve_job(next_job, description)
            elif verdict == "partial":
                next_job.poller.on_partial_match()
            else:
                next_job.poller.on_change_no_match()
            
            next_job.next_check_at = now + next_job.poller.interval
    
    async def resolve_job(self, job: WaitJob, description: str):
        """Condition met — wake OpenClaw."""
        job.status = "resolved"
        job.result_message = description
        del self.jobs[job.id]
        
        # Inject wake event into OpenClaw
        await inject_system_event(
            f"[smart_wait resolved] {job.criteria} → {description}"
        )
        
        # If linked to a task, auto-update it
        if job.task_id:
            await task_manager.add_message(
                job.task_id,
                f"Wait completed: {description}"
            )
```

## Wake Mechanism

### Option A: OpenClaw Gateway API (recommended)

OpenClaw's gateway exposes RPC endpoints. We can inject a system event directly:

```python
import httpx

async def inject_system_event(message: str, session_key: str = "agent:main:main"):
    """Inject a system event into the OpenClaw agent session."""
    async with httpx.AsyncClient() as client:
        await client.post(
            f"http://localhost:18789/api/agent",
            json={
                "sessionKey": session_key,
                "message": message,
            },
            headers={
                "Authorization": f"Bearer {OPENCLAW_TOKEN}"
            }
        )
```

### Option B: cron.wake (simpler, less direct)

```python
async def wake_via_cron(message: str):
    """Use OpenClaw's cron wake mechanism."""
    # This wakes the agent on next heartbeat with the message
    # Less direct but requires no gateway API knowledge
    ...
```

### Option C: File-based wake (most compatible)

Write a file that HEARTBEAT.md checks:

```python
def wake_via_file(message: str):
    """Write a wake file that the heartbeat checks."""
    with open("/home/alex/.openclaw/workspace/smart-wait-events.json", "w") as f:
        json.dump({"event": message, "timestamp": time.time()}, f)
```

Add to HEARTBEAT.md: "Check smart-wait-events.json; if present, read and clear it."

**Decision**: Start with Option C (file-based) for v1 since it requires zero OpenClaw internals knowledge. Upgrade to Option A once we understand the gateway API better.

## PTY Fast Path

For terminal targets, we can skip vision entirely for many common conditions:

```python
import re

class PTYMatcher:
    """Fast text-based matching for terminal output."""
    
    COMMON_PATTERNS = {
        "command_complete": r"\$\s*$",  # Shell prompt returned
        "error": r"(?i)(error|fail|fatal|exception|traceback)",
        "download_complete": r"(?i)(100%|download complete|saved|done)",
        "build_success": r"(?i)(build success|compiled|built in)",
        "build_fail": r"(?i)(build fail|compilation error|make.*error)",
    }
    
    @staticmethod
    def try_text_match(terminal_output: str, criteria: str) -> str | None:
        """Try to match criteria against terminal text. Returns match or None."""
        output_lower = criteria.lower()
        
        # Check common patterns
        for pattern_name, regex in PTYMatcher.COMMON_PATTERNS.items():
            if pattern_name in output_lower or any(
                word in output_lower for word in pattern_name.split("_")
            ):
                if re.search(regex, terminal_output):
                    match = re.search(regex, terminal_output)
                    return f"Matched '{pattern_name}': {match.group()}"
        
        # Simple substring check
        for word in criteria.split():
            if len(word) > 3 and word.lower() in terminal_output.lower():
                return f"Found '{word}' in terminal output"
        
        return None  # Fall back to vision
```
