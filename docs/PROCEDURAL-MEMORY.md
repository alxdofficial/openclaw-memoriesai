# Procedural Memory — Detailed Design (Phase 2)

## Overview

Procedural Memory turns passive screen recordings into a searchable knowledge base of "how the user does things." Instead of the agent figuring out every UI from scratch, it first checks: "Have I seen this done before?"

This component depends on [Memories AI](https://memories.ai) for video comprehension and semantic search.

## How It Works

### Recording Pipeline

```
User's Desktop
  │
  ▼
Screen Recorder (daemon)
  │ continuous recording, chunked into 5-min segments
  ▼
Local Buffer (~1 hour rolling)
  │
  ▼
Upload to Memories AI
  │ segments uploaded in background
  │ indexed for semantic search
  ▼
Memories AI Index
  │ searchable by natural language
  │ returns timestamps + extracted actions
```

### Query Pipeline

```
Agent needs to do something unfamiliar
  │
  ▼
memory_recall({ query: "how to add SSH key on GitHub" })
  │
  ▼
Memories AI Semantic Search
  │ finds relevant video segments
  ▼
Action Extraction (via VLM)
  │ converts video segments into step-by-step actions
  ▼
Returns to Agent:
  "On Feb 10, user: 1) opened github.com/settings/keys
   2) clicked New SSH key 3) pasted key 4) clicked Add"
  │
  ▼
Agent replays steps (adapting to current UI state)
```

## Screen Recorder Design

### Requirements
- Low CPU/memory footprint (it's always running)
- Chunk-based: 5-minute segments for parallel upload
- Privacy controls: exclude specific windows/apps
- Pause/resume capability
- Rolling buffer to limit disk usage

### Implementation (Linux/X11)

```python
import subprocess
import time
import os

class ScreenRecorder:
    """Continuous screen recorder using ffmpeg."""
    
    def __init__(self, 
                 output_dir: str = "~/.openclaw-memoriesai/recordings",
                 segment_duration: int = 300,  # 5 minutes
                 max_segments: int = 12,  # 1 hour rolling buffer
                 fps: int = 2,  # 2 FPS is enough for UI actions
                 resolution: str = "1280x720"):
        self.output_dir = os.path.expanduser(output_dir)
        self.segment_duration = segment_duration
        self.max_segments = max_segments
        self.fps = fps
        self.resolution = resolution
    
    def start(self):
        """Start recording in segments."""
        os.makedirs(self.output_dir, exist_ok=True)
        
        # ffmpeg segment recording
        # -f x11grab: capture X11 display
        # -r 2: 2 FPS (sufficient for UI actions, low CPU)
        # -s 1280x720: downscaled for storage efficiency
        # -segment_time 300: split every 5 minutes
        cmd = [
            "ffmpeg",
            "-f", "x11grab",
            "-r", str(self.fps),
            "-s", self.resolution,
            "-i", ":0.0",
            "-c:v", "libx264",
            "-preset", "ultrafast",  # minimal CPU usage
            "-crf", "28",  # reasonable quality
            "-f", "segment",
            "-segment_time", str(self.segment_duration),
            "-reset_timestamps", "1",
            os.path.join(self.output_dir, "segment_%04d.mp4")
        ]
        
        self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    def cleanup_old_segments(self):
        """Remove segments beyond the rolling buffer limit."""
        segments = sorted(
            [f for f in os.listdir(self.output_dir) if f.endswith('.mp4')],
            key=lambda f: os.path.getmtime(os.path.join(self.output_dir, f))
        )
        while len(segments) > self.max_segments:
            oldest = segments.pop(0)
            os.remove(os.path.join(self.output_dir, oldest))
```

### Why 2 FPS?

UI actions happen at human speed — clicks, typing, navigation. 2 FPS captures every meaningful state change while using ~1/15th the storage of 30 FPS video. A 5-minute segment at 2 FPS, 720p, CRF 28 is roughly 5-10 MB.

### Privacy Controls

```python
class PrivacyFilter:
    """Exclude sensitive windows from recording."""
    
    EXCLUDED_PATTERNS = [
        "1Password",
        "KeePass",
        "Private Browsing",
        "Incognito",
    ]
    
    # Could also blur specific regions, or pause recording
    # when certain windows are focused
```

## Memories AI Integration

### Upload Pipeline

```python
from memories import VideoAnalyzer

class MemoriesUploader:
    """Upload screen recording segments to Memories AI for indexing."""
    
    def __init__(self, api_key: str):
        self.analyzer = VideoAnalyzer(api_key=api_key)
    
    async def upload_segment(self, segment_path: str):
        """Upload and index a single segment."""
        result = await self.analyzer.analyze(
            video_url=segment_path,  # or upload bytes
            features=["summary", "search", "detect"],
            metadata={
                "source": "screen_recording",
                "host": "alxdws2",
                "display": ":0",
                "recorded_at": extract_timestamp(segment_path)
            }
        )
        return result
```

### Search Interface

```python
async def recall(query: str, limit: int = 3) -> list[dict]:
    """Search screen recording history for procedural knowledge."""
    
    # Search via Memories AI
    results = await analyzer.search(
        query=query,
        limit=limit,
        filters={"source": "screen_recording"}
    )
    
    # For each result, extract step-by-step actions
    extracted = []
    for result in results:
        # Get the relevant video segment
        segment = result.segment
        
        # Use MiniCPM-o to extract UI actions from the frames
        frames = extract_key_frames(segment.video_url, segment.start_ms, segment.end_ms)
        
        steps = []
        for i, (frame, next_frame) in enumerate(zip(frames, frames[1:])):
            action = await describe_action(frame, next_frame)
            if action:
                steps.append(action)
        
        extracted.append({
            "timestamp": segment.timestamp,
            "duration_seconds": (segment.end_ms - segment.start_ms) / 1000,
            "confidence": result.score,
            "summary": result.summary,
            "steps": steps,
            "video_url": segment.video_url,
            "video_start_ms": segment.start_ms,
            "video_end_ms": segment.end_ms,
        })
    
    return extracted

async def describe_action(frame_before: bytes, frame_after: bytes) -> str | None:
    """Use VLM to describe what action the user took between two frames."""
    response = await model.generate(
        prompt="""Look at these two consecutive screenshots. 
Describe the single UI action the user performed between them.
If nothing meaningful changed, respond with NONE.
Be specific: "Clicked the 'Save' button in the top-right" not "Clicked a button".""",
        images=[frame_before, frame_after]
    )
    if "NONE" in response:
        return None
    return response.strip()
```

## How the Agent Uses Procedural Memory

### Before attempting an unfamiliar task:

```
Agent thinking: "I need to add Alex's SSH key to GitHub. Let me check if I've 
seen this done before."

→ memory_recall({ query: "adding SSH key to GitHub" })
← {
    results: [{
      steps: [
        "Opened browser to github.com/settings/keys",
        "Clicked 'New SSH key'", 
        "Typed 'alxdws2' in the Title field",
        "Pasted key content in the Key field",
        "Clicked 'Add SSH key'"
      ],
      confidence: 0.92
    }]
  }

Agent: "Great, I have a playbook. Let me follow these steps, adapting as needed."
```

### When the UI doesn't match:

If the recalled steps reference a button that no longer exists (GitHub updated their UI), the agent falls back to visual reasoning with the main LLM. But it still has context from the memory ("I need to find the 'add key' functionality, it used to be a green button at top-right").

## Storage & Costs

| Component | Storage | Cost |
|-----------|---------|------|
| Screen recordings (1hr rolling, 2FPS, 720p) | ~60-120 MB | Local disk |
| Memories AI indexing | Depends on plan | API cost |
| Local action extraction (MiniCPM-o) | 0 | Local compute |

## Open Questions

1. **Memories AI API access**: What endpoints are available? Upload format? Search API?
2. **Real-time vs batch indexing**: Upload segments immediately or batch overnight?
3. **Cross-device**: If Alex uses multiple machines, can recordings be federated?
4. **Selective recording**: Should we only record when the agent is active, or always?
5. **Action extraction quality**: Is MiniCPM-o sufficient for describing UI actions between frames, or do we need a bigger model?
