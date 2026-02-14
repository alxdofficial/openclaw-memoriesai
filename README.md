# openclaw-memoriesai

MCP server giving OpenClaw persistent task memory, smart visual waiting, desktop control, and screen recording — all powered by a local vision model on your GPU.

## Quick Install

```bash
git clone https://github.com/alxdofficial/openclaw-memoriesai.git
cd openclaw-memoriesai
./install.sh
```

The installer handles everything:
- Python venv + dependencies
- Ollama + MiniCPM-o vision model
- System packages (xdotool, ffmpeg, fluxbox)
- Systemd service (auto-start, auto-restart)
- OpenClaw mcporter config (auto-discovered tools)

## What It Does

### Smart Wait
Delegate waiting to a local vision model. Monitor screens, windows, or terminals — your agent gets woken when a condition is met.

```
mcporter call memoriesai.smart_wait target=screen wake_when="build completed" timeout:=120
```

The agent's turn ends. The daemon watches in the background. When the condition is met (or times out), it fires `openclaw system event --mode now` to wake the agent back up.

- **MiniCPM-o 4.5** vision backbone via Ollama (~2s inference on RTX 3070)
- **Pixel-diff gate** — skips expensive vision calls when nothing changed
- **Adaptive polling** — speeds up on progress, backs off on static
- **PTY fast path** — regex matching for terminal output before touching GPU

### Task Memory
Track multi-step work across context compactions. Never lose your place.

```
mcporter call memoriesai.task_register name="Deploy app" plan='["build","test","deploy"]'
mcporter call memoriesai.task_update task_id=abc123 message="Build passed"
mcporter call memoriesai.task_list
```

### Desktop Control
Native GUI automation via xdotool — click, type, manage windows, take screenshots with AI descriptions.

```
mcporter call memoriesai.desktop_look prompt="Where is the submit button?"
mcporter call memoriesai.desktop_action action=click x=500 y=300
```

### Screen Recording
Capture video clips via ffmpeg.

```
mcporter call memoriesai.video_record duration:=10
```

## Architecture

```
┌─────────────────────────────────────┐
│  OpenClaw Agent                     │
│  (calls MCP tools via mcporter)     │
└──────────┬──────────────────────────┘
           │ stdio (per-call)
           ▼
┌──────────────────────┐   HTTP proxy   ┌─────────────────────────────┐
│  MCP Server (proxy)  │──────────────▶│  memoriesai daemon (:18790)  │
│  (spawned by         │               │  ├── Wait Engine (async)     │
│   mcporter per-call) │               │  ├── Task Manager (SQLite)   │
└──────────────────────┘               │  ├── Desktop Control         │
                                       │  ├── Video Recorder          │
                                       │  └── Vision (Ollama/GPU)     │
                                       └──────────┬──────────────────┘
                                                   │ on resolve/timeout
                                                   ▼
                                       openclaw system event --mode now
                                       (wakes the agent)
```

**Why the split?** MCP stdio servers are spawned per-call and die after. The wait engine needs to persist across calls. So: persistent daemon holds all state, MCP server is a thin proxy.

## Tools (11)

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

## Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| Python | 3.11+ | 3.12+ |
| GPU | None (CPU works, slower) | NVIDIA RTX 3060+ (8GB VRAM) |
| RAM | 8 GB | 16 GB |
| Disk | ~6 GB | ~10 GB |
| OS | Linux (X11) | Ubuntu 22.04+ |
| Display | Xvfb (headless) or physical | Xvfb :99 at 1920x1080 |

## Manual Setup

If you prefer not to use the installer:

```bash
# 1. Python
python3 -m venv .venv
.venv/bin/pip install -e .

# 2. Ollama + model
curl -fsSL https://ollama.com/install.sh | sh
ollama pull minicpm-v

# 3. System deps
sudo apt install xdotool ffmpeg fluxbox

# 4. Start daemon
DISPLAY=:99 PYTHONPATH=src .venv/bin/python3 -m openclaw_memoriesai.daemon

# 5. Configure mcporter (see install.sh for JSON config)
```

## Service Management

```bash
sudo systemctl status memoriesai-daemon    # Check status
sudo systemctl restart memoriesai-daemon   # Restart
journalctl -u memoriesai-daemon -f         # Follow logs
curl http://127.0.0.1:18790/health         # Health check
```

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DISPLAY` | `:99` | X11 display for screen capture |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `MEMORIESAI_VISION_MODEL` | `minicpm-v` | Vision model name |
| `MEMORIESAI_DATA_DIR` | `~/.openclaw-memoriesai` | Data directory |

## License

MIT
