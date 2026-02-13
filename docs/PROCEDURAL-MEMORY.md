# Memories AI Integration — Detailed Design

## Overview

Memories AI gives the agent video comprehension abilities that go beyond frame-by-frame screenshot analysis. Instead of sending 20 individual screenshots to Claude (massive token cost), the agent records a short video clip and sends it to Memories AI as a single API call — purpose-built for understanding temporal sequences, UI actions, and state changes.

Two modes of use:

1. **Record & Remember** (async) — Record something worth remembering for future reference
2. **Record & Understand** (sync) — Record something the agent needs to understand right now

## Mode 1: Record & Remember (Async)

The LLM is doing or watching something complex and thinks "this would be useful to remember for next time."

### Flow

```
LLM: "I just navigated a complex settings page. Let me record this for future reference."
  │
  ▼
video_record({
  target: "window:Firefox",
  duration: 30,
  prompt: "What are the steps to navigate to SSH key settings on GitHub?",
  mode: "remember",
  tags: ["github", "ssh", "settings"]
})
  │
  ▼ (returns immediately)
{ recording_id: "rec-456", status: "recording",
  message: "Recording 30s of Firefox. Will analyze and report back." }
  │
  │  LLM continues with other work...
  │
  ▼ (30s recording + Memories AI analysis)
Daemon: record → upload to Memories AI → get analysis
  │
  ▼ (inject system event via `openclaw system event --mode now`)
[system] video_record analysis complete (rec-456):
  "Steps to navigate to SSH key settings on GitHub:
   1) Click profile avatar in top-right corner
   2) Click 'Settings' from dropdown menu
   3) In left sidebar, click 'SSH and GPG keys' under 'Access'
   4) Page shows existing keys and 'New SSH key' button"
  Tags: github, ssh, settings
  │
  ▼
LLM wakes up, recognizes this as procedural knowledge.
Decides to save it (e.g., writes to MEMORY.md, creates a runbook, etc.)
```

### Key Characteristics
- **Async**: LLM doesn't wait. Recording and analysis happen in the background.
- **Persistent**: The analysis is meant to be saved by the LLM for future reference.
- **Videos stored**: Kept in Memories AI index for potential future search/re-analysis.
- **LLM decides what to save**: The system event just delivers the analysis. The LLM chooses whether and where to persist it (memory files, runbooks, etc.).

### When to use
- After completing a complex UI navigation ("let me record how I did that")
- While watching the user do something ("let me capture this workflow")
- When exploring an unfamiliar application ("record what I find")

## Mode 2: Record & Understand (Sync)

The LLM is mid-task and encounters something that requires video-level understanding — more than a single screenshot can convey.

### Flow

```
LLM: "There's a progress animation happening. I need to understand the current state."
  │
  ▼
video_understand({
  target: "window:Firefox",
  duration: 10,
  prompt: "What is happening on this page? Is the upload progressing and what percentage?"
})
  │
  ▼ (LLM BLOCKS — waits for result)
Daemon: record 10s → upload to Memories AI → get analysis → return
  │
  ▼ (tool result returned directly to LLM)
{ answer: "A file upload is in progress. The progress bar shows 73% complete.
   The filename is 'dataset.zip' (420 MB). Upload speed indicator shows ~2.4 MB/s.
   Estimated time remaining: ~45 seconds. No errors visible.",
  confidence: 0.94,
  duration_recorded: 10 }
  │
  ▼
LLM continues its task with this information.
Video is DISCARDED (not saved to index).
```

### Key Characteristics
- **Sync**: LLM waits for the result — this is a blocking tool call.
- **Ephemeral**: Video is not saved after analysis. It's a one-time understanding aid.
- **Replaces multi-frame screenshot analysis**: Instead of the LLM taking 20 screenshots and reasoning over them (expensive), one API call to Memories AI.
- **Returns directly**: No system event — the answer comes back as the tool result.

### When to use
- Progress bars, animations, loading sequences ("what percentage is this at?")
- Complex UI states that need temporal context ("is this page still loading or is it stuck?")
- Multi-step processes happening on screen ("what just happened in this terminal?")
- Any time >3-4 frames would be needed to understand the visual state

## Why Memories AI Over Direct LLM Vision?

| Approach | 10-second video (2 FPS = 20 frames) | Cost | Quality |
|----------|--------------------------------------|------|---------|
| Send 20 screenshots to Claude | 20 × ~200KB = ~4MB base64, thousands of vision tokens | $$$ | Frame-by-frame, misses temporal patterns |
| Send 20 screenshots to MiniCPM-o | Same but local | Free but slow (20 sequential inferences) | Same limitation |
| **Memories AI (1 video upload)** | **One API call, native video understanding** | **API cost (reasonable)** | **Understands motion, progress, state changes** |

Memories AI is purpose-built for video comprehension. It understands:
- **Temporal sequences**: "the user clicked A, then B, then C"
- **State transitions**: "the page went from loading to loaded"
- **Progress tracking**: "the bar went from 50% to 73% over 10 seconds"
- **Semantic search**: Find relevant moments in longer recordings

## Implementation

### Screen Recorder Module

```python
import subprocess
import os
import tempfile
import asyncio

class ScreenRecorder:
    """Record short video clips from Xvfb for Memories AI analysis."""
    
    def __init__(self, display: str = ":99"):
        self.display = display
    
    async def record_window(self, window_name: str, duration: int, 
                            fps: int = 5) -> str:
        """Record a specific window for N seconds. Returns path to mp4."""
        # Find window ID
        result = subprocess.run(
            ["xdotool", "search", "--name", window_name],
            capture_output=True, text=True,
            env={**os.environ, "DISPLAY": self.display}
        )
        if not result.stdout.strip():
            raise ValueError(f"Window not found: {window_name}")
        
        window_id = result.stdout.strip().split("\n")[0]
        
        # Get window geometry
        geom = subprocess.run(
            ["xdotool", "getwindowgeometry", "--shell", window_id],
            capture_output=True, text=True,
            env={**os.environ, "DISPLAY": self.display}
        )
        # Parse geometry for position + size
        
        output_path = tempfile.mktemp(suffix=".mp4")
        
        # Record using ffmpeg targeting the window region
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-f", "x11grab",
            "-r", str(fps),
            "-video_size", f"{width}x{height}",
            "-i", f"{self.display}+{x},{y}",
            "-t", str(duration),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            output_path,
            env={**os.environ, "DISPLAY": self.display},
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return output_path
    
    async def record_screen(self, duration: int, fps: int = 5) -> str:
        """Record the full Xvfb desktop for N seconds."""
        output_path = tempfile.mktemp(suffix=".mp4")
        
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-f", "x11grab",
            "-r", str(fps),
            "-video_size", "1920x1080",
            "-i", f"{self.display}.0",
            "-t", str(duration),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            output_path,
            env={**os.environ, "DISPLAY": self.display},
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return output_path
```

### Memories AI Client

```python
import httpx
import os

class MemoriesClient:
    """Client for Memories AI video analysis API."""
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("MEMORIES_AI_API_KEY")
        self.base_url = "https://api.memories.ai/v1"  # TBD — confirm with Alex
    
    async def analyze_video(self, video_path: str, prompt: str,
                            save: bool = False, tags: list[str] = None) -> dict:
        """Upload video and get analysis from Memories AI.
        
        Args:
            video_path: Path to mp4 file
            prompt: Question or focus area for analysis
            save: Whether to save in Memories AI index (True for remember mode)
            tags: Optional tags for organizing saved videos
        
        Returns:
            { "answer": str, "confidence": float, ... }
        """
        async with httpx.AsyncClient(timeout=120) as client:
            with open(video_path, "rb") as f:
                response = await client.post(
                    f"{self.base_url}/analyze",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files={"video": (os.path.basename(video_path), f, "video/mp4")},
                    data={
                        "prompt": prompt,
                        "save": str(save).lower(),
                        "tags": ",".join(tags) if tags else "",
                    }
                )
            response.raise_for_status()
            return response.json()
    
    async def search(self, query: str, limit: int = 3) -> list[dict]:
        """Search previously saved video analyses."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.base_url}/search",
                headers={"Authorization": f"Bearer {self.api_key}"},
                params={"q": query, "limit": limit}
            )
            response.raise_for_status()
            return response.json()["results"]
```

### MCP Tool Handlers

```python
# video_record — async (remember mode)
async def handle_video_record(params: dict) -> dict:
    target = params["target"]
    duration = params.get("duration", 30)
    prompt = params["prompt"]
    tags = params.get("tags", [])
    
    recording_id = generate_id()
    
    # Start recording in background — don't block
    asyncio.create_task(
        _record_and_analyze(recording_id, target, duration, prompt, tags, save=True)
    )
    
    return {
        "recording_id": recording_id,
        "status": "recording",
        "message": f"Recording {duration}s of {target}. Will analyze and report back."
    }

async def _record_and_analyze(recording_id, target, duration, prompt, tags, save):
    """Background: record → analyze → wake OpenClaw."""
    try:
        # Record
        if target == "screen":
            video_path = await recorder.record_screen(duration)
        else:
            window_name = target.replace("window:", "")
            video_path = await recorder.record_window(window_name, duration)
        
        # Analyze via Memories AI
        result = await memories_client.analyze_video(video_path, prompt, save=save, tags=tags)
        
        # Wake OpenClaw with the result
        message = (
            f"[video_record] Analysis complete ({recording_id}):\n"
            f"{result['answer']}\n"
            f"Tags: {', '.join(tags)}"
        )
        subprocess.run([
            "openclaw", "system", "event",
            "--text", message,
            "--mode", "now"
        ])
        
        # Cleanup video if not saved
        if not save:
            os.unlink(video_path)
    except Exception as e:
        subprocess.run([
            "openclaw", "system", "event",
            "--text", f"[video_record] Error ({recording_id}): {e}",
            "--mode", "now"
        ])


# video_understand — sync (ephemeral mode)
async def handle_video_understand(params: dict) -> dict:
    target = params["target"]
    duration = params.get("duration", 10)
    prompt = params["prompt"]
    
    # Record (blocking)
    if target == "screen":
        video_path = await recorder.record_screen(duration)
    else:
        window_name = target.replace("window:", "")
        video_path = await recorder.record_window(window_name, duration)
    
    # Analyze via Memories AI (blocking)
    result = await memories_client.analyze_video(video_path, prompt, save=False)
    
    # Cleanup — ephemeral, don't keep
    os.unlink(video_path)
    
    return {
        "answer": result["answer"],
        "confidence": result.get("confidence", 0.0),
        "duration_recorded": duration,
    }
```

## Data Flow Summary

```
                    ┌──────────────────────────┐
                    │     OpenClaw Main LLM     │
                    └──────┬──────────┬─────────┘
                           │          │
              video_record │          │ video_understand
              (async)      │          │ (sync, blocks)
                           ▼          ▼
                    ┌──────────────────────────┐
                    │   openclaw-memoriesai     │
                    │   daemon                  │
                    │                           │
                    │   Screen Recorder         │
                    │   (ffmpeg + Xvfb)         │
                    └──────┬──────────┬─────────┘
                           │          │
                           ▼          ▼
                    ┌──────────────────────────┐
                    │     Memories AI API       │
                    │                           │
                    │  - Video analysis         │
                    │  - Semantic search        │
                    │  - Temporal understanding │
                    └──────┬──────────┬─────────┘
                           │          │
              System event │          │ Direct tool result
              (async wake) │          │ (sync return)
                           ▼          ▼
                    ┌──────────────────────────┐
                    │     OpenClaw Main LLM     │
                    │                           │
                    │  Remember mode:           │
                    │  → saves to MEMORY.md     │
                    │    or runbooks            │
                    │                           │
                    │  Understand mode:          │
                    │  → uses answer to         │
                    │    continue current task  │
                    └──────────────────────────┘
```

## Open Questions

1. **Memories AI API format**: Need to confirm exact endpoints, auth method, upload format, and response schema. Alex to provide API docs/keys.
2. **Video duration limits**: What's the max video length Memories AI accepts per call? This affects the `duration` parameter bounds.
3. **Cost**: What does a typical 10-30s video analysis cost? This affects how aggressively the LLM should use `video_understand`.
4. **Search API**: For future use — can we search across previously saved `video_record` analyses? This would enable "have I seen this before?" queries without re-recording.
5. **Latency**: How long does Memories AI take to analyze a 10-30s clip? This directly affects `video_understand` blocking time.
