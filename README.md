# agentic-computer-use

Desktop Environment Task Manager (DETM) — an MCP server with hierarchical task tracking, smart visual waiting, GUI automation with NL grounding, pluggable vision backends, and screen recording. All local-first.

## Quick Install

```bash
git clone https://github.com/alxdofficial/openclaw-memoriesai.git
cd openclaw-memoriesai
./install.sh
```

The installer handles:
- Python venv + dependencies
- Ollama + vision model
- System packages (xdotool, ffmpeg, fluxbox)
- Systemd service (auto-start, auto-restart)
- OpenClaw mcporter config

## Architecture

```
OpenClaw LLM → DETM (task hierarchy) → Vision + GUI Agent → Desktop/Xvfb
```

Four layers:
1. **Task Management** — hierarchical: Task → Plan Items → Actions → Logs
2. **Smart Wait** — vision-based async monitoring with pixel-diff gate + adaptive polling
3. **GUI Agent** — NL-to-coordinates grounding (UI-TARS, Claude CU, or direct xdotool)
4. **Vision** — pluggable backends (Ollama, vLLM, Claude, passthrough)

```
┌──────────────────────┐   HTTP proxy   ┌──────────────────────────────┐
│  MCP Server (proxy)  │──────────────▶│  DETM daemon (:18790)        │
│  (spawned per-call   │               │  ├── Task Manager (hierarchy) │
│   by mcporter)       │               │  ├── Wait Engine (async)      │
└──────────────────────┘               │  ├── GUI Agent (NL grounding) │
                                       │  ├── Desktop Control (xdotool)│
                                       │  └── Vision (pluggable)       │
                                       └──────────────────────────────┘
```

## Tools (20)

### Task Management (Hierarchical)
| Tool | Description |
|------|-------------|
| `task_register` | Create task with plan items |
| `task_update` | Post message, change status, query state |
| `task_item_update` | Update plan item status (pending/active/completed/failed/skipped) |
| `task_log_action` | Log action under a plan item (cli/gui/wait/vision/reasoning) |
| `task_summary` | Item-level overview (default), actions, or full detail |
| `task_drill_down` | Expand one plan item to see actions + logs |
| `task_thread` | Legacy message thread view |
| `task_list` | List tasks by status |

### Smart Wait
| Tool | Description |
|------|-------------|
| `smart_wait` | Delegate visual monitoring to local model |
| `wait_status` | Check active wait jobs |
| `wait_update` | Refine condition or extend timeout |
| `wait_cancel` | Cancel a wait job |

### GUI Agent
| Tool | Description |
|------|-------------|
| `gui_do` | Execute NL or coordinate action ("click the Export button") |
| `gui_find` | Locate element by description, return coords |

### Desktop Control
| Tool | Description |
|------|-------------|
| `desktop_action` | Raw xdotool: click, type, keys, windows |
| `desktop_look` | Screenshot + vision description |
| `video_record` | Record screen/window clip |

### System
| Tool | Description |
|------|-------------|
| `health_check` | Daemon + vision + system status |
| `memory_search/read/append` | Workspace memory files |

## Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| Python | 3.11+ | 3.12+ |
| GPU | None (CPU works) | NVIDIA RTX 3060+ (8GB VRAM) |
| RAM | 8 GB | 16 GB |
| OS | Linux (X11) | Ubuntu 22.04+ |
| Display | Xvfb (headless) or physical | Xvfb :99 at 1920x1080 |

## Manual Setup

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
DISPLAY=:99 .venv/bin/python3 -m agentic_computer_use.daemon

# 5. Configure mcporter (see install.sh for JSON config)
```

## Daemon — Start / Stop / Restart

The DETM daemon is the long-running process that hosts the wait engine, task manager, GUI agent, and dashboard. Everything else (MCP server, CLI) talks to it over HTTP.

### Systemd (production)

```bash
sudo systemctl start detm-daemon        # Start
sudo systemctl stop detm-daemon         # Stop
sudo systemctl restart detm-daemon      # Restart
sudo systemctl status detm-daemon       # Check status
journalctl -u detm-daemon -f            # Follow logs
```

The systemd unit is installed by `./install.sh` and auto-starts on boot.

### Manual / dev mode

```bash
# Start in foreground with debug logging
ACU_DEBUG=1 DISPLAY=:99 PYTHONPATH=src .venv/bin/python3 -m agentic_computer_use.daemon

# Start in a tmux session (persists after SSH disconnect)
tmux new-session -d -s detm-dev
tmux send-keys -t detm-dev 'ACU_DEBUG=1 DISPLAY=:99 PYTHONPATH=src .venv/bin/python3 -m agentic_computer_use.daemon' Enter

# Stop (Ctrl-C in the terminal, or):
tmux send-keys -t detm-dev C-c

# Restart
tmux send-keys -t detm-dev C-c
sleep 1
tmux send-keys -t detm-dev 'ACU_DEBUG=1 DISPLAY=:99 PYTHONPATH=src .venv/bin/python3 -m agentic_computer_use.daemon' Enter

# Attach to see live output
tmux attach -t detm-dev
```

### Health check

```bash
curl http://127.0.0.1:18790/health      # JSON status of daemon + vision + GPU
```

## Dashboard

The dashboard is a built-in web UI served by the daemon — no separate process needed.

### Access

The daemon binds to `127.0.0.1:18790`, so the dashboard is available at:

```
http://127.0.0.1:18790/dashboard
```

**Local access** — open in a browser on the same machine, or via noVNC.

**Remote access** — the daemon only listens on localhost. To access from another machine (e.g. your Mac over Tailscale), use an SSH tunnel:

```bash
# On your Mac:
ssh -L 18790:127.0.0.1:18790 alex@<tailscale-ip>

# Then open in browser:
open http://localhost:18790/dashboard
```

### Dashboard features

- **Task list sidebar** — all tasks with status badges and progress bars
- **Task tree** — expandable plan items with nested actions
- **Action details** — click any action to expand; GUI/wait actions show:
  - Before/after screenshot thumbnails (click to open full-res lightbox)
  - Click coordinates and confidence scores (GUI actions)
  - Wait verdict reasoning, elapsed time, target (wait actions)
- **Live screen viewer** — MJPEG stream of the desktop or a task's window
- **Auto-refresh** — polls for updates every few seconds

### Dashboard API endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /dashboard` | Dashboard HTML page |
| `GET /api/tasks` | List tasks (query: `?status=active&limit=20`) |
| `GET /api/tasks/{id}` | Task detail (query: `?detail=actions`) |
| `GET /api/tasks/{id}/items/{ord}` | Drill into plan item |
| `GET /api/tasks/{id}/screen` | MJPEG stream for task's window |
| `GET /api/screen` | MJPEG stream of full desktop |
| `GET /api/screenshots/{filename}` | Serve action screenshot files |
| `GET /api/waits` | Active wait jobs |
| `GET /api/health` | Health check |

### Screenshots

Action screenshots (GUI before/after, wait before/after) are saved to:

```
~/.agentic-computer-use/screenshots/
```

Files are named `{action_id}_{role}.jpg` and `{action_id}_{role}_thumb.jpg`. The dashboard fetches thumbnails via `/api/screenshots/` and opens full-res in a lightbox on click.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DISPLAY` | `:1` | X11 display |
| `ACU_VISION_BACKEND` | `ollama` | ollama, vllm, claude, passthrough |
| `ACU_VISION_MODEL` | `minicpm-v` | Ollama model name |
| `ACU_VLLM_URL` | `http://localhost:8000` | vLLM API endpoint |
| `ACU_GUI_AGENT_BACKEND` | `direct` | direct, uitars, claude_cu |
| `ACU_DEBUG` | `0` | Enable verbose debug logging |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama API endpoint |
| `ANTHROPIC_API_KEY` | — | Required for Claude vision/GUI backends |

## Logs & Debugging

```bash
./dev.sh logs                           # Live colored debug tail
tail -f ~/.agentic-computer-use/logs/debug.log
ACU_DEBUG=1 detm-daemon                 # Start with debug logging
```

## License

MIT
