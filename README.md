# openclaw-memoriesai

MCP server giving OpenClaw (or any MCP client) persistent task memory, smart visual waiting, desktop control, and screen recording.

## Features

### Smart Wait (Vision-based)
Delegate waiting to a local vision model. Monitor screens, windows, or terminals — get woken when a condition is met.
- **MiniCPM-o 4.5** backbone via Ollama (1.6s warm inference on RTX 3070)
- **Pixel-diff gate** skips evaluation when screen hasn't changed
- **Adaptive polling** — speeds up when getting close, slows down on static
- **PTY fast path** — regex matching for terminal output (skips vision for CLI)
- **Job context windows** — rolling frame history for temporal reasoning

### Task Memory
Persistent task tracking across context boundaries. Never lose track of multi-step work.
- Register tasks with plans, report progress, query state
- **AI distillation** — MiniCPM-o summarizes task history on query
- **Plan progress inference** — heuristic tracking of completed/current/remaining steps
- Auto-updates from smart_wait resolutions

### Desktop Control
Native GUI automation via xdotool.
- Mouse: click, double-click, drag, move
- Keyboard: type text, press key combinations
- Windows: list, find, focus, resize, move, close
- Vision: screenshot + AI description of current state

### Screen Recording
Capture video clips via ffmpeg for debugging or procedural memory.

## Tools (11 MCP tools)

| Tool | Description |
|------|-------------|
| `smart_wait` | Monitor screen/window/terminal, wake on condition |
| `wait_status` | Check active wait jobs |
| `wait_update` | Refine a wait condition after early wake |
| `wait_cancel` | Cancel a wait job |
| `task_register` | Start tracking a multi-step task |
| `task_update` | Report progress or query task state |
| `task_list` | List active/completed tasks |
| `health_check` | System diagnostics |
| `desktop_action` | Click, type, manage windows |
| `desktop_look` | Screenshot + vision analysis |
| `video_record` | Record screen clips |

## Setup

### Prerequisites
- Python 3.11+
- Ollama with `minicpm-v` model
- Xvfb (headless) or X11 display
- xdotool
- ffmpeg (for video recording)

### Install

```bash
# Clone
git clone https://github.com/alxdofficial/openclaw-memoriesai.git
cd openclaw-memoriesai

# Create venv and install
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Install Ollama + model
curl -fsSL https://ollama.com/install.sh | sh
ollama pull minicpm-v

# Install xdotool
sudo apt install xdotool
```

### Configure with OpenClaw (mcporter)

Create `config/mcporter.json` in your OpenClaw workspace:

```json
{
  "mcpServers": {
    "memoriesai": {
      "command": "/path/to/openclaw-memoriesai/.venv/bin/python3",
      "args": ["-m", "openclaw_memoriesai.server"],
      "cwd": "/path/to/openclaw-memoriesai",
      "env": {
        "DISPLAY": ":99",
        "PYTHONPATH": "/path/to/openclaw-memoriesai/src"
      }
    }
  }
}
```

### Test

```bash
# Run tests
DISPLAY=:99 .venv/bin/python3 -m pytest tests/ -v

# Call tools via mcporter
mcporter call memoriesai.health_check
mcporter call memoriesai.task_register name="Test" plan='["step1","step2"]'
mcporter call memoriesai.desktop_look prompt="What's on screen?"
mcporter call memoriesai.video_record duration=5
```

## Architecture

```
OpenClaw Gateway
  │ MCP (stdio)
  ▼
openclaw-memoriesai daemon
  ├── MCP Server (mcp[cli])
  ├── Wait Manager (async event loop)
  │   ├── Frame Capture (python-xlib / Xvfb)
  │   ├── Pixel-Diff Gate (numpy)
  │   ├── PTY Fast Path (regex)
  │   └── MiniCPM-o Evaluation (Ollama)
  ├── Task Manager (SQLite)
  │   └── AI Distillation (MiniCPM-o)
  ├── Desktop Control (xdotool)
  ├── Video Recorder (ffmpeg)
  └── Wake Dispatch (openclaw system event)
```

## Hardware

| Component | Requirement |
|-----------|-------------|
| GPU | NVIDIA RTX 3060+ recommended (8GB VRAM) |
| RAM | 8GB minimum (5GB model + 3GB system) |
| CPU | Any modern x86_64 works for CPU-only (slower) |
| Disk | ~6GB for model + recordings |

## License

MIT
