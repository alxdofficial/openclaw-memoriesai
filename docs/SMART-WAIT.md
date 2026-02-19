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

For Smart Wait, we ask for short reasoning text plus a final structured verdict line (`FINAL_JSON`). Outputs remain compact enough that CPU inference can still be viable, while structured parsing improves reliability.

## Headless Setup (Xvfb)

The primary development target is a **headless Ubuntu server** (no physical monitor). All GUI-based watch targets (browser windows, desktop apps) run inside Xvfb — a virtual X11 framebuffer.

### Xvfb Configuration

```bash
# Install
sudo apt install xvfb

# Start a virtual display (1920x1080, 24-bit color)
Xvfb :99 -screen 0 1920x1080x24 -ac &
export DISPLAY=:99

# Or use xvfb-run to wrap a single command
xvfb-run --server-args="-screen 0 1920x1080x24" firefox
```

For persistent use, run Xvfb as a systemd service:

```ini
# /etc/systemd/system/xvfb.service
[Unit]
Description=X Virtual Frame Buffer
After=network.target

[Service]
ExecStart=/usr/bin/Xvfb :99 -screen 0 1920x1080x24 -ac
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now xvfb
```

**Important**: All processes that need a display (browsers, GUI apps, our frame grabber) must use `DISPLAY=:99`. OpenClaw's headless Chrome already works this way.

### How frame capture works on headless

The frame capture pipeline is identical to a physical display — Xvfb provides a standard X11 server, so `python-xlib`, `xdotool`, `import`, etc. all work normally. The only difference is there's no physical monitor rendering the pixels.

```
Xvfb (:99) ← virtual framebuffer in RAM
  │
  ├── Firefox (running inside Xvfb)
  ├── File Manager (running inside Xvfb)
  └── Any GUI app
  │
  ▼
python-xlib reads pixels from Xvfb
  → same API as physical X11 display
  → no performance difference
```

### Window management on headless

To find and manage windows in Xvfb:

```bash
export DISPLAY=:99

# List all windows
xdotool search --name ""

# Find Firefox window
xdotool search --name "Firefox"

# Get window geometry
xdotool getwindowgeometry <window_id>

# Take a screenshot of a specific window
import -window <window_id> screenshot.png

# Take a screenshot of the entire virtual desktop
import -window root screenshot.png
```

## Frame Capture Pipeline

### Step 1: Raw Frame Grabbing

```python
import os
import Xlib.display
import Xlib.X

# Ensure we're using the Xvfb display
DISPLAY = os.environ.get("DISPLAY", ":99")

def capture_window_x11(window_id: int) -> bytes:
    """Capture a window from Xvfb using X11. ~5ms per frame."""
    display = Xlib.display.Display(DISPLAY)
    window = display.create_resource_object('window', window_id)
    geom = window.get_geometry()
    raw = window.get_image(0, 0, geom.width, geom.height, Xlib.X.ZPixmap, 0xffffffff)
    return raw.data

def capture_full_screen() -> bytes:
    """Capture the entire Xvfb virtual desktop."""
    display = Xlib.display.Display(DISPLAY)
    root = display.screen().root
    geom = root.get_geometry()
    raw = root.get_image(0, 0, geom.width, geom.height, Xlib.X.ZPixmap, 0xffffffff)
    return raw.data

# Fallback: ImageMagick import (slower, ~50ms)
import subprocess
def capture_window_import(window_id: int, output_path: str):
    subprocess.run(
        ["import", "-window", hex(window_id), output_path],
        env={**os.environ, "DISPLAY": DISPLAY}
    )

def find_window_by_name(name: str) -> int | None:
    """Find a window ID by title substring using xdotool."""
    result = subprocess.run(
        ["xdotool", "search", "--name", name],
        capture_output=True, text=True,
        env={**os.environ, "DISPLAY": DISPLAY}
    )
    if result.stdout.strip():
        return int(result.stdout.strip().split("\n")[0])
    return None

# PTY: just read the buffer — no display needed
def capture_pty(session_id: str) -> str:
    """Read terminal buffer — no vision needed, no Xvfb needed."""
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

### Step 4: Per-Job Context Window

Each watch job maintains a rolling context window — not just the current frame, but a history of recent frames, previous model verdicts, and timing metadata. This gives the model temporal reasoning ("progress was at 60%, then 75%, now 90%") rather than stateless single-frame evaluation.

```python
from collections import deque
from dataclasses import dataclass

@dataclass
class FrameSnapshot:
    data: bytes          # full resolution JPEG
    thumbnail: bytes     # 360p thumbnail for history
    timestamp: float

class JobContext:
    """Rolling context window for a single watch job."""
    
    def __init__(self, max_frames: int = 4, max_verdicts: int = 3):
        self.frames: deque[FrameSnapshot] = deque(maxlen=max_frames)
        self.verdicts: deque[dict] = deque(maxlen=max_verdicts)
        self.started_at: float = time.time()
        self.last_change_at: float = time.time()
    
    def add_frame(self, frame_jpeg: bytes, timestamp: float):
        thumbnail = resize_jpeg(frame_jpeg, max_dim=360, quality=60)
        self.frames.append(FrameSnapshot(
            data=frame_jpeg, thumbnail=thumbnail, timestamp=timestamp
        ))
        self.last_change_at = timestamp
    
    def add_verdict(self, verdict: str, description: str, timestamp: float):
        self.verdicts.append({
            "verdict": verdict, "description": description, "timestamp": timestamp
        })
    
    def build_prompt(self, criteria: str) -> tuple[str, list[bytes]]:
        """Build evaluation prompt with full temporal context."""
        elapsed = time.time() - self.started_at
        since_change = time.time() - self.last_change_at
        
        verdict_lines = "\n".join(
            f"- [{format_ago(v['timestamp'])}] {v['verdict']}: {v['description']}"
            for v in self.verdicts
        ) or "None yet (first evaluation)."
        
        text = f"""You are monitoring a screen for a specific condition.

CONDITION: {criteria}
MONITORING DURATION: {format_duration(elapsed)}
TIME SINCE LAST SCREEN CHANGE: {format_duration(since_change)}

PREVIOUS OBSERVATIONS (oldest first):
{verdict_lines}

Frame history: {len(self.frames)} frames attached (oldest → newest).
The last image is the current frame at full resolution.

Based on the progression and current state, evaluate whether the condition is satisfied.

Decision policy:
- `resolved` only if condition is explicitly met in the current frame.
- `watching` if evidence is absent/ambiguous or condition not met.
- `partial` only for clear progress that is still incomplete.

Output contract:
1) 2–6 lines brief reasoning.
2) Final line only:
`FINAL_JSON: {"decision":"resolved|watching|partial","confidence":0.0,"evidence":["..."],"summary":"..."}`
"""
        
        # Thumbnails for history frames + full res for current
        images = [f.thumbnail for f in list(self.frames)[:-1]]
        if self.frames:
            images.append(self.frames[-1].data)  # current = full resolution
        
        return text, images
```

**Memory budget per job:**

| Component | Size | Notes |
|-----------|------|-------|
| Current frame (full) | ~80 KB | 720p JPEG |
| 3 history thumbnails | ~15 KB each = 45 KB | 360p JPEG |
| Status text + verdicts | ~500 bytes | |
| **Total per job** | **~125 KB** | Fits easily in MiniCPM-o context |

For 5 concurrent jobs: ~625 KB total — well within limits.

**Why context windows are better than single-frame:**

1. **Temporal reasoning** — "bar was at 90% and is now gone" = download finished
2. **Stall detection** — "stuck at 90% for 60s" = possible hang, report as PARTIAL with warning
3. **False positive prevention** — brief glitches in one frame won't trigger false YES
4. **Error detection** — "progress bar was moving, now a dialog appeared" = error
5. **Smarter polling** — model can infer "advancing at 5%/10s, should finish in ~20s"

### Step 5: Model Evaluation

```python
async def evaluate_condition(
    job_context: JobContext,
    criteria: str,
    model_client: OllamaClient
) -> tuple[str, str]:  # (verdict, description)
    """Ask MiniCPM-o if the condition is met, with full temporal context."""
    
    prompt_text, images = job_context.build_prompt(criteria)
    
    response = await model_client.generate(
        model="minicpm-v",
        prompt=prompt_text,
        images=images
    )
    
    # Preferred: parse FINAL_JSON payload on the last line
    verdict, description = parse_structured_verdict(response)
    if verdict is not None:
        return verdict, description

    # Fallback for legacy/free-form responses
    return parse_legacy_verdict(response)
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

**Decision**: Start with Option A (`openclaw system event` CLI). It's purpose-built for this exact use case, requires zero custom OpenClaw code, and wakes the agent within seconds. Three lines of Python.

## PTY Targets (Current Behavior)

Smart Wait now uses a **vision-only evaluator** across GUI and CLI/PTy targets.
There is no regex/text fast path in the runtime.

Current PTY behavior:
- `target=pty:<session_id>` links the wait to a process/session identity.
- Frame capture is still visual (desktop/screen pipeline), not terminal-buffer parsing.
- For best reliability, prefer `target=window:<terminal_window_id_or_name>` so the model sees only the terminal window.

Future improvement (optional): bind PTY waits directly to a resolved terminal window at creation time.
