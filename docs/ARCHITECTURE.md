# Architecture

## Overview

**agentic-computer-use** is a Desktop Environment Task Manager (DETM) — an MCP server with a persistent HTTP daemon that provides hierarchical task tracking, smart visual waiting, GUI automation with natural language grounding, and pluggable vision backends.

```
OpenClaw LLM → DETM (task hierarchy) → Vision + GUI Agent → Desktop/Xvfb
```

Five layers:
1. **Task Management** — hierarchical: Task → Plan Items → Actions → Logs
2. **Smart Wait** — vision-based async monitoring with pixel-diff gate + adaptive polling
3. **GUI Agent** — NL-to-coordinates grounding (UI-TARS, Claude CU, or direct xdotool)
4. **Vision** — pluggable backends (Ollama, vLLM, Claude, OpenRouter, passthrough)
5. **Display Manager** — per-task virtual displays (Xvfb isolation)

## System Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    OpenClaw Gateway                          │
│                                                              │
│  MCP tools available to the model:                           │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────┐ │
│  │ Task Mgmt    │ │ Smart Wait   │ │ GUI Agent            │ │
│  │ task_register│ │ smart_wait   │ │ gui_do (NL→click)    │ │
│  │ task_summary │ │ wait_status  │ │ gui_find (NL→coords) │ │
│  │ task_update  │ │ wait_update  │ │                      │ │
│  │ task_item_*  │ │ wait_cancel  │ │ Desktop              │ │
│  │ task_log_*   │ │              │ │ desktop_action       │ │
│  │ task_drill_* │ │              │ │ desktop_look         │ │
│  └──────┬───────┘ └──────┬──────┘ │ video_record         │ │
│         │                │        └──────────┬───────────┘ │
└─────────┼────────────────┼───────────────────┼─────────────┘
          │     MCP (stdio)                    │
          ▼                ▼                   ▼
┌──────────────────────────────────────────────────────────────┐
│            agentic-computer-use MCP server                   │
│            (thin proxy → daemon HTTP calls)                  │
└──────────────────────┬───────────────────────────────────────┘
                       │ HTTP (127.0.0.1:18790)
                       ▼
┌──────────────────────────────────────────────────────────────┐
│                   DETM Daemon (persistent)                   │
│                                                              │
│  ┌────────────────────┐  ┌───────────────────────────────┐  │
│  │  Task Manager       │  │  Wait Engine                  │  │
│  │                     │  │                               │  │
│  │  tasks              │  │  Wait job queue               │  │
│  │  ├── plan_items     │  │  Pixel-diff gate              │  │
│  │  │   ├── actions    │  │  Adaptive polling             │  │
│  │  │   │   └── logs   │  │  Condition eval (via Vision)  │  │
│  │  │   └── ...        │  │  Wake dispatch → OpenClaw     │  │
│  │  └── ...            │  │                               │  │
│  └─────────┬──────────┘  └──────────┬────────────────────┘  │
│            │                        │                        │
│  ┌─────────▼────────────────────────▼────────────────────┐  │
│  │              Vision Backend (pluggable)                │  │
│  │                                                       │  │
│  │  ┌──────────┐ ┌──────────┐ ┌────────┐ ┌───────────┐ ┌────────────┐ │  │
│  │  │ Ollama   │ │ vLLM     │ │ Claude │ │Passthrough│ │ OpenRouter │ │  │
│  │  │(default) │ │(UI-TARS) │ │ (API)  │ │ (no eval) │ │(cloud,any) │ │  │
│  │  └──────────┘ └──────────┘ └────────┘ └───────────┘ └────────────┘ │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌──────────────────┐  ┌──────────────────────────────────┐ │
│  │  GUI Agent        │  │  Desktop Control                 │ │
│  │                   │  │                                  │ │
│  │  NL → grounding   │  │  xdotool: click, type, keys     │ │
│  │  ┌────────────┐   │  │  X11/Xvfb screen capture        │ │
│  │  │ UI-TARS    │   │  │  Window management               │ │
│  │  │ Claude CU  │   │  │  Video recording                 │ │
│  │  │ Direct     │   │  │                                  │ │
│  │  └────────────┘   │  │                                  │ │
│  └──────────────────┘  └──────────────────────────────────┘ │
│                                                              │
│  ┌──────────────────┐  ┌──────────────────────────────────┐ │
│  │  Display Manager  │  │  Stuck Detection Loop            │ │
│  │                   │  │                                  │ │
│  │  Per-task Xvfb    │  │  60s check interval              │ │
│  │  displays (:100+) │  │  Wake OpenClaw via system event  │ │
│  │  Xlib caching     │  │  Resume packet with context      │ │
│  └──────────────────┘  └──────────────────────────────────┘ │
│                                                              │
│  ┌──────────────────┐  ┌──────────────────────────────────┐ │
│  │  SQLite Database  │  │  Web Dashboard (:18790/dashboard)│ │
│  │  ~/.agentic-      │  │                                  │ │
│  │  computer-use/    │  │  Task tree + message feed        │ │
│  │  data.db          │  │  Live MJPEG screen stream        │ │
│  │                   │  │  Task controls (cancel/pause)    │ │
│  └──────────────────┘  └──────────────────────────────────┘ │
│                                                              │
│  ┌──────────────────────────────────────────────────────────┐│
│  │  Debug Log — ~/.agentic-computer-use/logs/debug.log      ││
│  └──────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────┘
```

## Component Details

### MCP Server Layer

The MCP server (`server.py`) is a thin proxy. It defines tool schemas and forwards all calls to the persistent daemon over HTTP. OpenClaw launches it via stdio transport.

The daemon (`daemon.py`) runs independently on port 18790 and maintains state across MCP server restarts.

### Task Manager (Hierarchical)

Data model:

```
Task
├── id, name, status, metadata, created_at, updated_at
└── PlanItem[] (ordered by ordinal)
    ├── ordinal, title, status, started_at, completed_at, duration_seconds
    └── Action[]
        ├── action_type (cli|gui|wait|vision|reasoning|other)
        ├── summary, status, input_data, output_data, duration_ms
        └── ActionLog[]
            └── log_type, content, created_at
```

**Default view** is item-level: plan items with status + action counts. Drill-down expands individual items to show actions and logs.

**Status flow**:
- Task: active → paused/completed/failed/cancelled
- Plan Item: pending → active → completed/failed/skipped

### Smart Wait Engine

Vision-based async monitoring. The LLM delegates a visual wait, the daemon monitors, and wakes the LLM when the condition is met.

**Frame capture pipeline**:
```
Target (window or screen)
  │
  ▼  [run_in_executor — Xlib off-thread, serialized by _CAPTURE_LOCK]
Frame Grabber → Pixel-Diff Gate → Vision Backend Evaluation → Decision
  │             (320px downsample   (evaluate NL condition)
  │              before diff, ~30×  persistent HTTP client,
  │              faster)            no reconnect per call
  ▼  [run_in_executor — PIL off-thread]
JPEG / thumbnail encode
```

**Adaptive polling**:
- Static screen (no pixel diff): 5-10s intervals
- Changing screen: 1-2s intervals
- Partial match: 0.5-1s burst mode
- 30s+ static: forced re-evaluation

**Parallel job evaluation**: all overdue wait jobs are evaluated concurrently via `asyncio.gather()`. Vision I/O runs in parallel; Xlib captures are serialized by a module-level `asyncio.Lock` (`_CAPTURE_LOCK`). `_resolve_job` / `_timeout_job` guard against double-resolution from concurrent evaluations.

### Vision Backend (Pluggable)

Configured via `ACU_VISION_BACKEND`:

| Backend | Model | Use Case | Cost |
|---------|-------|----------|------|
| `ollama` (default) | Configurable (`ACU_VISION_MODEL`, default `minicpm-v`) | Local, free | Free (GPU) |
| `vllm` | UI-TARS-1.5-7B, Qwen, etc. | Best accuracy for grounding + wait | Free (GPU) |
| `claude` | Claude via Anthropic API | Zero-GPU fallback | ~$0.002/eval |
| `openrouter` | Any model (Gemini Flash, Haiku, etc.) | Cloud, no GPU, cheapest option | ~$0.0002/eval |
| `passthrough` | None | Debug — no evaluation | Free |

All backends implement `evaluate_condition(prompt, images)` and `check_health()`.

All backends use a **persistent `httpx.AsyncClient`** (module-level singleton) so TLS connections are reused across calls — saves 100–300 ms per cloud call.

**OpenRouter recommendation:** `google/gemini-2.0-flash-001` at $0.10/$0.40 per million tokens (~$0.00022/eval) is 10× cheaper than Claude Haiku and has strong multimodal vision for UI screenshots. Set `OPENROUTER_API_KEY` + `ACU_VISION_BACKEND=openrouter` + `ACU_OPENROUTER_VISION_MODEL=google/gemini-2.0-flash-001`.

### GUI Agent (NL Grounding)

Natural language → screen coordinates → xdotool execution.

```
"click the Export button"
  │
  ▼  [run_in_executor — Xlib/PIL off-thread, same pattern as SmartWait]
Screenshot capture → Grounding backend → (x, y) coordinates → xdotool click
                     (persistent HTTP client,
                      no TLS reconnect per call)
```

Four backends (`ACU_GUI_AGENT_BACKEND`):

| Backend | Model | Accuracy | Cost |
|---------|-------|----------|------|
| `uitars` | UI-TARS-1.5-7B (OpenRouter cloud or Ollama local) | 61.6% ScreenSpot-Pro | ~$0.0003/call or free |
| `claude_cu` | Claude computer_use API | ~27.7% | API cost |
| `omniparser` | YOLO + Florence-2 + Claude Haiku picker | High for icon/element grounding | Free (GPU) + Haiku per pick |
| `direct` | None (coords required) | N/A | Free |

The `uitars` backend auto-selects mode: if `OPENROUTER_API_KEY` is set, it uses OpenRouter's hosted model (fast, no GPU needed). Otherwise it uses Ollama with per-request `keep_alive` (default 5m) so UI-TARS auto-unloads from VRAM after idle, freeing space for minicpm-v smart_wait polls.

All backends use a **persistent `httpx.AsyncClient`** (module-level singleton) — no TLS reconnect per grounding call. The `uitars` backend maintains two clients (one for OpenRouter, one for Ollama). `omniparser`'s Claude Haiku picker also uses a persistent client.

`gui_do` accepts both NL ("click the Export button") and explicit coords ("click(847, 523)").

**OmniParser pipeline** (`omniparser` backend):
```
screenshot → YOLO (detect bounding boxes)
           → Florence-2 batch caption all crops in one GPU forward pass
           → numbered overlay image
           → Claude Haiku: "which element matches the description?" → N
           → center of box N → (x, y)
```

### Desktop Control

`xdotool`-based execution layer for X11/Xvfb. Commands are **chained** in single subprocess calls to reduce spawn overhead:
- `mouse_click_at(x, y)` → `xdotool mousemove --sync x y click 1` (1 subprocess, was 2)
- `focus_window(wid)` → `xdotool windowfocus --sync wid windowraise wid` (1 subprocess, was 2)
- `mouse_drag(x1,y1, x2,y2)` → 2 subprocesses (was 4)
- Keyboard: type text, press key combos
- Windows: list, find, resize, move, close
- Screen: capture, record video clips

### Display Manager (Per-Task Isolation)

Each task gets its own Xvfb virtual display at registration time (`display/manager.py`). This isolates tasks from each other — one task's GUI actions don't interfere with another's screen.

- `allocate_display(task_id)` — starts Xvfb on the next free display number (`:100`, `:101`, ...)
- `release_display(task_id)` — kills Xvfb and cleans up when task reaches terminal status
- `get_xlib_display(display_str)` — cached Xlib connections for fast frame capture
- `cleanup_all()` — tears down all managed displays on daemon shutdown

Task metadata stores `display`, `display_num`, and `display_resolution`. All tools that accept `task_id` (desktop_action, gui_do, smart_wait, etc.) automatically resolve the task's display.

### Stuck Detection

Background loop in the daemon (`stuck_detection_loop()`, 60s interval) monitors active tasks:

1. Skip tasks with active smart waits (they're legitimately waiting)
2. If `now - task.updated_at >= 300s` and no active waits → task is stuck
3. Build a resume packet: task state, plan items, active item expanded with action details + logs, last 5 messages, wait state
4. Inject `[task_stuck_resume] {json}` into OpenClaw via `openclaw system event --mode now`
5. Cooldown: no duplicate alerts within 300s per task

### Web Dashboard

Built-in web UI served by the daemon at `/dashboard`. No separate process.

Components (`dashboard/components/`):
- **task-list.js** — sidebar with status badges and progress bars; **download button** for completed/cancelled task recordings (MP4)
- **task-tree.js** — expandable plan items → actions → logs with screenshots, coordinates, lightbox; shows which vision model/backend was used per action
- **screen-viewer.js** — polled JPEG live view (2 fps) + **replay mode** (scrub through recorded frames frame-by-frame) + recording controls (start/stop with elapsed timer)
- **message-feed.js** — color-coded message feed (agent=blue, user=white, wait=yellow, system=gray/green, errors=red)

Task controls in the tree header: **Pause**, **Resume**, **Cancel** buttons that POST status changes to the daemon.

**Frame recording & video export:** While a task is active the daemon captures JPEG frames at ~2 fps into `~/.agentic-computer-use/recordings/{task_id}/`. On task completion or cancellation, frames are encoded to H.264 MP4 (ffmpeg, CRF 28, 2 fps) and the raw frames are deleted. The MP4 is available for download via the dashboard. Deleting a task also deletes the video. Only failed tasks discard frames immediately (no video created).

### Database

SQLite via `aiosqlite`. Single file at `~/.agentic-computer-use/data.db`.

Tables: `tasks`, `plan_items`, `actions`, `action_logs`, `task_messages`, `wait_jobs`.

### Logging

Debug log at `~/.agentic-computer-use/logs/debug.log`. Enable with `ACU_DEBUG=1` or `--debug` flag. Both the human and Claude Code can tail the log:

```bash
./dev.sh logs          # live colored tail
tail -f ~/.agentic-computer-use/logs/debug.log
```

## Configuration

All environment variables use the `ACU_*` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `ACU_VISION_BACKEND` | `ollama` | Vision backend: ollama, vllm, claude, openrouter, passthrough |
| `ACU_VISION_MODEL` | `minicpm-v` | Ollama vision model name |
| `ACU_VLLM_URL` | `http://localhost:8000` | vLLM API endpoint |
| `ACU_VLLM_MODEL` | `ui-tars-1.5-7b` | vLLM model name |
| `ACU_CLAUDE_VISION_MODEL` | `claude-sonnet-4-20250514` | Claude vision model |
| `OPENROUTER_API_KEY` | (none) | OpenRouter API key — for both vision and GUI-TARS grounding |
| `ACU_OPENROUTER_VISION_MODEL` | `anthropic/claude-haiku-4-5` | OpenRouter model for vision (recommend `google/gemini-2.0-flash-001`) |
| `OLLAMA_KEEP_ALIVE` | `10m` | How long Ollama keeps vision model in VRAM between calls |
| `ACU_GUI_AGENT_BACKEND` | `direct` | GUI grounding: direct, uitars, claude_cu, omniparser |
| `ACU_UITARS_OLLAMA_MODEL` | `0000/ui-tars-1.5-7b` | Ollama model for local UI-TARS grounding |
| `ACU_UITARS_KEEP_ALIVE` | `5m` | Ollama keep_alive for UI-TARS (frees VRAM after idle) |
| `ACU_DIFF_MAX_WIDTH` | `320` | Downsample width for pixel-diff computation (lower = faster, less accurate) |
| `ACU_DEBUG` | `0` | Enable verbose debug logging |
| `ACU_WORKSPACE` | (none) | Workspace directory for memory files |
| `DISPLAY` | `:99` | X11 display for screen capture (system shared display) |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API URL |
| `ANTHROPIC_API_KEY` | (none) | Required for Claude vision/GUI backends |
