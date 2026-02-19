# agentic-computer-use

Desktop Environment Task Manager (DETM) — an MCP server with hierarchical task tracking, smart visual waiting, GUI automation with NL grounding, pluggable vision backends, per-task virtual displays, and a live web dashboard. All local-first.

## Quick Install

```bash
git clone https://github.com/alxdofficial/openclaw-memoriesai.git
cd openclaw-memoriesai
./install.sh
```

The installer handles:
- Python venv + dependencies
- Ollama + vision model
- System packages (xdotool, ffmpeg, fluxbox, Xvfb)
- Systemd service (auto-start, auto-restart)
- OpenClaw mcporter config

## Architecture

```
OpenClaw LLM → DETM (task hierarchy) → Vision + GUI Agent → Desktop/Xvfb
```

Five layers:
1. **Task Management** — hierarchical: Task → Plan Items → Actions → Logs
2. **Smart Wait** — vision-based async monitoring with pixel-diff gate + adaptive polling
3. **GUI Agent** — NL-to-coordinates grounding (UI-TARS, Claude CU, or direct xdotool)
4. **Vision** — pluggable backends (Ollama, vLLM, Claude, passthrough)
5. **Display Manager** — per-task virtual displays (Xvfb isolation)

```
┌──────────────────────┐   HTTP proxy   ┌──────────────────────────────┐
│  MCP Server (proxy)  │──────────────▶│  DETM daemon (:18790)        │
│  (spawned per-call   │               │  ├── Task Manager (hierarchy) │
│   by mcporter)       │               │  ├── Wait Engine (async)      │
└──────────────────────┘               │  ├── GUI Agent (NL grounding) │
                                       │  ├── Desktop Control (xdotool)│
                                       │  ├── Display Manager (Xvfb)   │
                                       │  ├── Stuck Detection Loop     │
                                       │  ├── Web Dashboard            │
                                       │  └── Vision (pluggable)       │
                                       └──────────────────────────────┘
```

## Tools (20)

### Task Management (Hierarchical)
| Tool | Description |
|------|-------------|
| `task_register` | Create task with plan items. Each task gets its own virtual display. |
| `task_update` | Post message (narration), change status, query state |
| `task_item_update` | Update plan item status (pending/active/completed/failed/skipped) |
| `task_log_action` | Log action under a plan item (cli/gui/wait/vision/reasoning) |
| `task_summary` | Item-level overview (default), actions, full, or focused (expand only active item) |
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
| Display | Xvfb (headless) or physical | Xvfb for headless servers |

## Manual Setup

```bash
# 1. Python
python3 -m venv .venv
.venv/bin/pip install -e .

# 2. Ollama + model
curl -fsSL https://ollama.com/install.sh | sh
ollama pull minicpm-v

# 3. System deps
sudo apt install xdotool ffmpeg fluxbox xvfb

# 4. Start daemon
DISPLAY=:99 .venv/bin/python3 -m agentic_computer_use.daemon

# 5. Configure mcporter (see install.sh for JSON config)
```

## Daemon — Start / Stop / Restart

The DETM daemon is the long-running process that hosts the wait engine, task manager, GUI agent, display manager, stuck detection, and dashboard. Everything else (MCP server, CLI) talks to it over HTTP.

### dev.sh (development)

The recommended way to run the daemon during development:

```bash
./dev.sh               # Start (or restart) daemon in tmux with debug logging
./dev.sh stop          # Kill the tmux session
./dev.sh logs          # Tail the debug log in current terminal
./dev.sh status        # Health check + tmux session status
```

The daemon runs inside a tmux session called `detm-dev` so it persists after SSH disconnect. Attach to watch live output:

```bash
tmux attach -t detm-dev       # Watch live output
# Ctrl-B D to detach (daemon keeps running)
```

### Systemd (production)

```bash
sudo systemctl start detm-daemon        # Start
sudo systemctl stop detm-daemon         # Stop
sudo systemctl restart detm-daemon      # Restart
sudo systemctl status detm-daemon       # Check status
journalctl -u detm-daemon -f            # Follow logs
```

The systemd unit is installed by `./install.sh` and auto-starts on boot.

### Manual

```bash
# Start in foreground with debug logging
ACU_DEBUG=1 DISPLAY=:99 PYTHONPATH=src .venv/bin/python3 -m agentic_computer_use.daemon
```

### Health check

```bash
curl http://127.0.0.1:18790/health      # JSON status of daemon + vision + GPU
```

## Dashboard

The dashboard is a built-in web UI served by the daemon — no separate process needed. It starts and stops with the daemon.

### Access

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
  - Task control buttons: **Pause**, **Resume**, **Cancel** in the tree header
- **Action details** — click any action to expand; GUI/wait actions show:
  - Before/after screenshot thumbnails (click to open full-res lightbox)
  - Click coordinates and confidence scores (GUI actions)
  - Wait verdict reasoning, elapsed time, target (wait actions)
- **Message feed** — color-coded message stream below the task tree:
  - Blue: LLM narration (`LLM -> task`)
  - White: User messages (`User -> LLM`)
  - Yellow: Smart wait events (`SmartWait -> task`)
  - Gray: Lifecycle events (`System -> task`)
  - Green: Progress updates (`System -> task`)
  - Purple: Plan changes (`LLM -> plan`)
  - Red: Stuck alerts (`System -> alert`)
- **Live screen viewer** — MJPEG stream of the task's per-task virtual display
- **Recording controls** — start/stop screen recording per task
- **Auto-refresh** — polls for updates every 2 seconds

### Dashboard API endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/dashboard` | GET | Dashboard HTML page |
| `/api/tasks` | GET | List tasks (`?status=active&limit=20`) |
| `/api/tasks/{id}` | GET | Task detail (`?detail=items\|actions\|full\|focused`) |
| `/api/tasks/{id}/messages` | GET | Task message feed (`?limit=50`) |
| `/api/tasks/{id}/status` | POST | Change task status (`{"status":"cancelled"}`) |
| `/api/tasks/{id}/items/{ord}` | GET | Drill into plan item |
| `/api/tasks/{id}/screen` | GET | MJPEG stream for task's display |
| `/api/tasks/{id}/record/start` | POST | Start screen recording |
| `/api/tasks/{id}/record/stop` | POST | Stop screen recording |
| `/api/tasks/{id}/record/status` | GET | Recording status |
| `/api/screen` | GET | MJPEG stream of full desktop |
| `/api/screenshots/{filename}` | GET | Serve action screenshot files |
| `/api/recordings/{filename}` | GET | Serve recording files |
| `/api/waits` | GET | Active wait jobs |
| `/api/health` | GET | Health check |

### Screenshots

Action screenshots (GUI before/after, wait before/after) are saved to:

```
~/.agentic-computer-use/screenshots/
```

Files are named `{action_id}_{role}.jpg` and `{action_id}_{role}_thumb.jpg`. The dashboard fetches thumbnails via `/api/screenshots/` and opens full-res in a lightbox on click.

## Per-Task Virtual Displays

Each task gets its own Xvfb display at registration time. This isolates tasks from each other — one task's GUI actions don't interfere with another's screen.

Pass `display_width` and `display_height` in task metadata to override the default 1280x720:

```bash
# Via MCP tool:
task_register(name="Edit video", plan=[...], metadata={"display_width": 1920, "display_height": 1080})

# The task's display is stored in metadata as "display" (e.g., ":100")
# All tools that accept task_id automatically target the correct display
```

Displays are released automatically when a task reaches a terminal status (completed, failed, cancelled).

## LLM Guidance (SKILL.md)

The file `skill/SKILL.md` is the prompt that teaches OpenClaw's LLM how to use DETM tools. It covers:
- Tool reference and lifecycle patterns
- When to use GUI vs CLI
- Per-task display usage
- Narration requirements (writing reasoning to task history)
- Dashboard awareness (the human can cancel tasks)
- Stuck detection and automatic resumption

When updating tools or behavior, keep SKILL.md in sync — it's what the LLM actually reads.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DISPLAY` | `:1` | X11 display (fallback for tasks without per-task display) |
| `ACU_VISION_BACKEND` | `ollama` | ollama, vllm, claude, passthrough |
| `ACU_VISION_MODEL` | `minicpm-v` | Ollama model name |
| `ACU_VLLM_URL` | `http://localhost:8000` | vLLM API endpoint |
| `ACU_VLLM_MODEL` | `ui-tars-1.5-7b` | vLLM model name |
| `ACU_CLAUDE_VISION_MODEL` | `claude-sonnet-4-20250514` | Claude vision model |
| `ACU_GUI_AGENT_BACKEND` | `direct` | direct, uitars, claude_cu |
| `ACU_DEBUG` | `0` | Enable verbose debug logging |
| `ACU_WORKSPACE` | `~/.openclaw/workspace` | Workspace directory for memory files |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama API endpoint |
| `ANTHROPIC_API_KEY` | -- | Required for Claude vision/GUI backends |

## Repository Layout

```
src/agentic_computer_use/
  server.py              # MCP server (thin HTTP proxy to daemon)
  daemon.py              # Persistent HTTP daemon (aiohttp)
  config.py              # Configuration from env vars
  db.py                  # SQLite schema + helpers
  debug.py               # Colored debug logging
  screenshots.py         # Screenshot file management
  task/
    manager.py           # Hierarchical task CRUD, stuck detection, resume packets
  wait/
    engine.py            # Smart wait job queue + polling loop
    poller.py            # Adaptive polling interval
  capture/
    screen.py            # X11 screen capture via python-xlib
  desktop/
    control.py           # xdotool wrapper (click, type, windows)
  display/
    manager.py           # Per-task Xvfb display lifecycle
  gui/
    agent.py             # NL-to-coordinates grounding (UI-TARS, Claude CU)
  vision/
    __init__.py          # Pluggable vision backend interface
  video/
    recorder.py          # Screen recording (ffmpeg)
  dashboard/
    index.html           # Dashboard HTML
    style.css            # Dark terminal theme
    app.js               # Main app: polling, fetch helpers, wiring
    components/
      task-list.js       # Sidebar task list
      task-tree.js       # Expandable plan item tree with action details
      screen-viewer.js   # MJPEG stream + recording controls
      message-feed.js    # Color-coded message feed

skill/
  SKILL.md               # LLM prompt — teaches OpenClaw how to use DETM

docs/
  ARCHITECTURE.md        # Detailed architecture with ASCII diagrams
  TOOLS.md               # Complete tool parameter reference
  ROADMAP.md             # Development phases and progress
  SMART-WAIT.md          # Smart wait design: diff gate, adaptive polling, context windows
  TASK-MEMORY.md         # Task memory design: distillation, recovery
  TASK-MEMORY-RECOVERY-SPEC.md  # Stuck detection + resume packet spec
  PROBLEMS.md            # Problem statements this project solves
  RESEARCH.md            # Research notes on existing solutions
  PROCEDURAL-MEMORY.md   # Memories AI video comprehension design
  COMPARISON-OPENCLAW-COWORK-SIMULAR.md  # Feature comparison
  diagrams/              # Draw.io architecture diagrams

tests/
  test_integration.py    # 9 integration tests (verdict parser, diff gate, task lifecycle, stuck detection)
```

## Logs & Debugging

```bash
./dev.sh logs                           # Live colored debug tail
tail -f ~/.agentic-computer-use/logs/debug.log
ACU_DEBUG=1 detm-daemon                 # Start with debug logging
```

## Storage

| Path | Contents |
|------|----------|
| `~/.agentic-computer-use/data.db` | SQLite database (tasks, plan_items, actions, action_logs, task_messages, wait_jobs) |
| `~/.agentic-computer-use/screenshots/` | Action screenshots (before/after for GUI and wait actions) |
| `~/.agentic-computer-use/recordings/` | Screen recordings |
| `~/.agentic-computer-use/logs/debug.log` | Debug log (when ACU_DEBUG=1) |

## Tests

```bash
.venv/bin/python -m pytest tests/ -v
```

9 integration tests covering: structured verdict parser, pixel-diff gate, adaptive poller, job context prompt, task lifecycle, status normalization, hierarchical task model, stuck detection with resume packets.

## License

MIT
