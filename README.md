# agentic-computer-use

Desktop Environment Task Manager (DETM) — an MCP server that gives AI agents the ability to see and control a desktop. Hierarchical task tracking, autonomous GUI agent with visual grounding, smart visual waiting, and a live web dashboard. All local-first.

## Quick Install

```bash
git clone https://github.com/alxdofficial/openclaw-memoriesai.git
cd openclaw-memoriesai
OPENROUTER_API_KEY=sk-or-... ./install.sh
```

The installer handles everything:
- Python venv + dependencies
- System packages (xdotool, ffmpeg, XFCE4, Xvfb, scrot, x11vnc, noVNC)
- Virtual display + VNC (headless servers) or real display detection
- DETM daemon as a systemd service (auto-start on boot)
- OpenClaw MCP server registration (auto-discovers DETM tools)
- Browser installation if none found

You need an [OpenRouter API key](https://openrouter.ai/keys) for the GUI agent (Gemini Flash + UI-TARS grounding).

## Update

```bash
cd openclaw-memoriesai && git pull && ./install.sh
```

The installer is idempotent — safe to re-run anytime.

## Uninstall

```bash
# Stop services
sudo systemctl stop detm-daemon detm-xvfb detm-vnc detm-novnc detm-desktop
sudo systemctl disable detm-daemon detm-xvfb detm-vnc detm-novnc detm-desktop

# Remove systemd units
sudo rm /etc/systemd/system/detm-*.service
sudo systemctl daemon-reload

# Remove data
rm -rf ~/.agentic-computer-use

# Remove repo
rm -rf ~/openclaw-memoriesai

# (Optional) Remove MCP server entry
openclaw mcp unset agentic-computer-use
```

## Dashboard

The daemon serves a web dashboard at `http://127.0.0.1:18790/dashboard`. It shows:
- Task list with status and progress
- Expandable plan items with nested actions and screenshots
- Live desktop viewer (see what the agent is doing in real time)
- Message feed (agent narration, wait events, system messages)
- Cancel/pause tasks from the UI
- AI API cost tracking

### Accessing from a remote server (e.g. DigitalOcean)

The daemon only listens on localhost. To access from your laptop or phone:

**SSH tunnel (recommended):**
```bash
ssh -L 18790:localhost:18790 -L 6080:localhost:6080 user@your-server-ip
# Then open http://localhost:18790/dashboard in your browser
# VNC: http://localhost:6080/vnc.html
```

**Cloudflare tunnel (no SSH needed):**
```bash
# On the server:
cloudflared tunnel --url http://localhost:18790
# Opens a public https URL you can access from any device
```

## VNC (see the desktop)

On headless servers, `install.sh` sets up a virtual display with VNC:

| Service | Port | Description |
|---------|------|-------------|
| `detm-xvfb` | -- | Virtual display `:99` at 1920x1080 |
| `detm-vnc` | 5901 | Native VNC (use any VNC client) |
| `detm-novnc` | 6080 | Browser-based VNC (works on mobile) |

**Access via browser:**
```
http://localhost:6080/vnc.html
```

**Access via native VNC client:**
```
localhost:5901
```

Both require an SSH tunnel if accessing remotely (see above).

## Architecture

```
OpenClaw LLM → DETM (task hierarchy) → GUI Agent (Gemini + UI-TARS) → Desktop/Xvfb
```

Five layers:
1. **Task Management** — hierarchical: Task → Plan Items → Actions → Logs
2. **Smart Wait** — vision-based async monitoring with adaptive polling
3. **GUI Agent** — Gemini Flash supervisor + UI-TARS grounding (precise cursor placement)
4. **Vision** — pluggable backends (Ollama, vLLM, Claude, OpenRouter, passthrough)
5. **Display** — shared system display visible in VNC and the dashboard

## GUI Agent

The `gui_agent` is the main tool for desktop interaction. It uses:
- **Gemini Flash** as the supervisor (decides what to do, two-pass thinking)
- **UI-TARS-7B** as the grounding model (finds where on screen to click)
- **Multi-View Point ensembling** (MVP) for more accurate cursor placement
- **Independent verification** — checks task completion before signaling done

Give it a whole subtask, not individual clicks:

```python
# Good — delegate a subtask
gui_agent(instruction="Open Chrome, navigate to Google Flights, search for flights from NYC to London on March 28", timeout=120)

# Bad — micromanage
gui_agent(instruction="Click the search bar")
gui_agent(instruction="Type NYC to London")
# ... this is 5x slower
```

## Tools

### Core Tools
| Tool | Description |
|------|-------------|
| `gui_agent` | Autonomous GUI agent for any desktop interaction (clicks, typing, navigation, forms). Handles 10-30 step workflows. |
| `desktop_look` | Screenshot — you interpret the image yourself |
| `desktop_action` | Raw control: click(x,y), type, key press, window management |
| `smart_wait` | Vision-based async monitoring ("wake when export finishes") |

### Task Management
| Tool | Description |
|------|-------------|
| `task_register` | Create task with plan items |
| `task_update` | Post message, change status, query state |
| `task_item_update` | Update plan item status |
| `task_plan_append` | Add new plan items mid-task |
| `task_log_action` | Log action under a plan item |
| `task_summary` | Task overview at various detail levels |

### Other
| Tool | Description |
|------|-------------|
| `mavi_understand` | Record screen + ask a question about the video |
| `video_record` | Record screen/window clip |
| `health_check` | Daemon + vision + system status |

## Sub-Agents

DETM supports OpenClaw sub-agents for specialized workflows (e.g. LinkedIn automation). Sub-agents are **not deployed by default** — they're opt-in.

### Deploy sub-agents
```bash
DETM_DEPLOY_AGENTS=1 ./install.sh
```

### How sub-agents work
- The `main` agent is always the default — all messages route there
- Sub-agents are spawned explicitly: `/subagents spawn linkedin-test "do X"`
- Each sub-agent has its own workspace, SOUL.md, and AGENTS.md
- Sub-agents share DETM tools via mcporter

### If you get stuck in a sub-agent
Sub-agents should not intercept your messages unless explicitly bound. If you're stuck:

```bash
# Check current agent bindings
openclaw agents bindings

# Remove a binding
openclaw agents unbind <agent-id> --channel <channel-name>

# List all agents
openclaw agents list
```

### Creating a new sub-agent
1. Create a directory in `openclaw/agents/<agent-name>/`
2. Add `SOUL.md` (agent personality) and `AGENTS.md` (tool instructions)
3. Run `DETM_DEPLOY_AGENTS=1 ./install.sh`

## OpenClaw Setup

After running `install.sh`, DETM is registered as an MCP server. To use it through OpenClaw:

```bash
# Set model (must include openrouter/ prefix)
openclaw models set openrouter/google/gemini-2.5-flash

# Write API key for the gateway
echo "OPENROUTER_API_KEY=sk-or-v1-YOUR-KEY" > ~/.openclaw/.env

# Set gateway mode
openclaw config set gateway.mode local

# Start the gateway
OPENROUTER_API_KEY=sk-or-v1-YOUR-KEY openclaw gateway --force --auth none
```

**Alternative**: Run `openclaw configure` interactively to set provider, model, and API key in one flow.

Verify DETM tools are visible:
```bash
openclaw mcp list
# Should show: agentic-computer-use

openclaw agent --agent main -m "What DETM tools do you have?"
# Should list: task_register, gui_agent, desktop_look, desktop_action, etc.
```

**Note**: Use `openrouter/google/gemini-2.5-flash` (not `-preview`). OpenClaw may strip the `openrouter/` prefix from preview model IDs, causing a 400 error.

## Common Issues

### Daemon won't start / port already in use
```bash
# Kill stale processes on the daemon port
fuser -k 18790/tcp
# Then restart
sudo systemctl restart detm-daemon
# Or use dev.sh
./dev.sh
```

### Virtual display not working
```bash
# Check all display services
sudo systemctl status detm-xvfb detm-vnc detm-novnc detm-desktop

# Restart display stack
sudo systemctl restart detm-xvfb detm-desktop detm-vnc detm-novnc

# Verify display is working
DISPLAY=:99 xdpyinfo | head -5
```

### Black screen in VNC
The XFCE screensaver can blank the display. The installer disables it, but on older installs:
```bash
DISPLAY=:99 killall xfce4-screensaver
sudo systemctl restart detm-desktop
```

### GUI agent times out
- Default timeout is 180s. For complex tasks, set `timeout=300`
- Check if OpenRouter API key is valid: `curl http://127.0.0.1:18790/health`
- The two-pass thinking architecture uses ~2 API calls per action step

### Dashboard shows no tasks
- Make sure you're calling `task_register` before any desktop tools
- Check the daemon is running: `curl http://127.0.0.1:18790/health`

### OpenClaw doesn't see DETM tools
```bash
# Check MCP server is registered
openclaw mcp list
# Should show: agentic-computer-use

# If missing, re-register it
openclaw mcp set agentic-computer-use '{"command":"'$HOME'/openclaw-memoriesai/.venv/bin/python3","args":["-m","agentic_computer_use.server"],"cwd":"'$HOME'/openclaw-memoriesai","env":{"DISPLAY":":99","PYTHONPATH":"'$HOME'/openclaw-memoriesai/src"}}'

# Restart the gateway to pick up changes
kill -HUP $(pgrep -f openclaw-gateway)
```

### Docker volumes filling disk
OSWorld benchmark runs create Docker volumes. Clean them periodically:
```bash
docker volume prune -f
docker container prune -f
```

## Manage Services

```bash
# Restart everything
sudo systemctl restart detm-daemon detm-xvfb detm-vnc detm-novnc detm-desktop

# Just the daemon (picks up code changes)
sudo systemctl restart detm-daemon

# Check status
sudo systemctl status detm-daemon
journalctl -u detm-daemon -f    # tail logs

# Development mode (runs from repo, debug logging)
./dev.sh                         # start in tmux
./dev.sh stop                    # stop
./dev.sh logs                    # tail logs
./dev.sh status                  # health check
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DISPLAY` | `:99` | X11 display |
| `OPENROUTER_API_KEY` | -- | Required for GUI agent (Gemini + UI-TARS) |
| `ACU_OPENROUTER_LIVE_MODEL` | `google/gemini-3-flash-preview` | Supervisor model |
| `ACU_UITARS_OPENROUTER_MODEL` | `bytedance/ui-tars-1.5-7b` | Grounding model |
| `ACU_MVP_ENABLED` | `0` | Multi-View Point ensembling (`1` to enable) |
| `ACU_GUI_AGENT_BACKEND` | `direct` | Grounding backend (uitars, qwen3vl, direct). Set to `uitars` for UI-TARS grounding. |
| `ACU_VISION_BACKEND` | `ollama` | Vision backend for smart_wait |
| `ACU_DEBUG` | `0` | Verbose debug logging |
| `ACU_STUCK_DETECTION` | `0` | Stuck task detection (`1` to enable) |
| `MAVI_API_KEY` | -- | Memories.AI API key for `mavi_understand` |

## Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| Python | 3.11+ | 3.12+ |
| RAM | 8 GB | 16 GB |
| OS | Linux (X11) | Ubuntu 22.04+ |
| GPU | None (CPU works) | NVIDIA GPU for local vision models |

## Storage

| Path | Contents |
|------|----------|
| `~/.agentic-computer-use/data.db` | SQLite database (tasks, actions, logs) |
| `~/.agentic-computer-use/screenshots/` | Action screenshots |
| `~/.agentic-computer-use/recordings/` | Screen recordings |
| `~/.agentic-computer-use/logs/debug.log` | Debug log |

## License

MIT
